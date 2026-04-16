from __future__ import annotations

import hashlib
import json
import math
import os
import random
import secrets
import sqlite3
from functools import wraps
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from flask import Flask, g, jsonify, redirect, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "travel_plan.db"
TZ_OFFSET_HOURS = 8
AMAP_API_URL = "https://restapi.amap.com/v3/geocode/geo"
ADMIN_ROUTE_TOKEN_KEY = "admin_route_token"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "travel-plan-dev-secret")
init_done = False


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_: Any) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def ensure_column(db: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = db.execute(f"PRAGMA table_info({table})").fetchall()
    names = {row[1] for row in cols}
    if column not in names:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_locked INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            start_time TEXT NOT NULL DEFAULT '00:00',
            end_time TEXT NOT NULL DEFAULT '23:59',
            location_name TEXT,
            latitude REAL,
            longitude REAL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS itinerary_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            day_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            annotation_offset REAL NOT NULL DEFAULT 0,
            connector_length_adjust REAL NOT NULL DEFAULT 0,
            annotation_side TEXT NOT NULL DEFAULT 'auto',
            title TEXT NOT NULL,
            item_type TEXT NOT NULL DEFAULT 'activity',
            place TEXT,
            transport_mode TEXT,
            from_place TEXT,
            to_place TEXT,
            latitude REAL,
            longitude REAL,
            note TEXT,
            FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS trip_daily_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            day_date TEXT NOT NULL,
            location_name TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(trip_id, day_date),
            FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    ensure_column(db, "users", "is_locked", "is_locked INTEGER NOT NULL DEFAULT 0")
    db.execute("UPDATE users SET is_locked = 0 WHERE is_locked IS NULL")
    ensure_column(db, "trips", "user_id", "user_id INTEGER")
    db.execute("CREATE INDEX IF NOT EXISTS idx_trips_user_id ON trips(user_id)")
    ensure_column(db, "trips", "location_name", "location_name TEXT")
    ensure_column(db, "trips", "latitude", "latitude REAL")
    ensure_column(db, "trips", "longitude", "longitude REAL")
    ensure_column(db, "trips", "start_time", "start_time TEXT NOT NULL DEFAULT '00:00'")
    ensure_column(db, "trips", "end_time", "end_time TEXT NOT NULL DEFAULT '23:59'")
    ensure_column(db, "itinerary_items", "item_type", "item_type TEXT NOT NULL DEFAULT 'activity'")
    ensure_column(db, "itinerary_items", "transport_mode", "transport_mode TEXT")
    ensure_column(db, "itinerary_items", "from_place", "from_place TEXT")
    ensure_column(db, "itinerary_items", "to_place", "to_place TEXT")
    ensure_column(db, "itinerary_items", "latitude", "latitude REAL")
    ensure_column(db, "itinerary_items", "longitude", "longitude REAL")
    ensure_column(db, "itinerary_items", "end_day_date", "end_day_date TEXT")
    ensure_column(db, "itinerary_items", "price", "price REAL NOT NULL DEFAULT 0")
    ensure_column(db, "itinerary_items", "annotation_offset", "annotation_offset REAL NOT NULL DEFAULT 0")
    ensure_column(db, "itinerary_items", "connector_length_adjust", "connector_length_adjust REAL NOT NULL DEFAULT 0")
    ensure_column(db, "itinerary_items", "annotation_side", "annotation_side TEXT NOT NULL DEFAULT 'auto'")
    db.commit()
    db.close()


def get_setting_value(key: str) -> str | None:
    db = sqlite3.connect(DB_PATH)
    try:
        row = db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row and row[0] is not None else None
    finally:
        db.close()


def set_setting_value(key: str, value: str) -> None:
    db = sqlite3.connect(DB_PATH)
    try:
        db.execute(
            """
            INSERT INTO app_settings(key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, value),
        )
        db.commit()
    finally:
        db.close()


def get_admin_route_token() -> str:
    token = (get_setting_value(ADMIN_ROUTE_TOKEN_KEY) or "").strip()
    if token:
        return token
    token = secrets.token_urlsafe(10).replace("-", "a").replace("_", "b")
    set_setting_value(ADMIN_ROUTE_TOKEN_KEY, token)
    return token


def date_range(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        return []
    days: list[str] = []
    cur = start
    while cur <= end:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_time_or_none(value: Any) -> datetime.time | None:
    if value is None or value == "":
        return None
    try:
        return datetime.strptime(str(value), "%H:%M").time()
    except ValueError:
        return None


def parse_local_datetime_or_none(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def time_to_minutes(t: datetime.time) -> int:
    return t.hour * 60 + t.minute


def is_valid_coords(lat: float, lon: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180


def normalize_angle(angle: float) -> float:
    while angle < 0:
        angle += 360
    while angle >= 360:
        angle -= 360
    return angle


def calc_sun_time(day: date, latitude: float, longitude: float, is_sunrise: bool) -> float | None:
    day_of_year = day.timetuple().tm_yday
    lng_hour = longitude / 15.0

    if is_sunrise:
        t = day_of_year + ((6 - lng_hour) / 24)
    else:
        t = day_of_year + ((18 - lng_hour) / 24)

    m = (0.9856 * t) - 3.289
    l = m + (1.916 * math.sin(math.radians(m))) + (0.020 * math.sin(math.radians(2 * m))) + 282.634
    l = normalize_angle(l)

    ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(l))))
    ra = normalize_angle(ra)

    l_quadrant = math.floor(l / 90) * 90
    ra_quadrant = math.floor(ra / 90) * 90
    ra += l_quadrant - ra_quadrant
    ra /= 15

    sin_dec = 0.39782 * math.sin(math.radians(l))
    cos_dec = math.cos(math.asin(sin_dec))

    lat_rad = math.radians(latitude)
    zenith = math.radians(90.833)
    cos_h = (math.cos(zenith) - (sin_dec * math.sin(lat_rad))) / (cos_dec * math.cos(lat_rad))

    if cos_h > 1 or cos_h < -1:
        return None

    if is_sunrise:
        h = 360 - math.degrees(math.acos(cos_h))
    else:
        h = math.degrees(math.acos(cos_h))
    h /= 15

    local_mean_time = h + ra - (0.06571 * t) - 6.622
    ut = (local_mean_time - lng_hour) % 24
    local_time = (ut + TZ_OFFSET_HOURS) % 24
    return local_time


def calc_sunrise_sunset(day: date, latitude: float, longitude: float) -> tuple[int, int]:
    sunrise_h = calc_sun_time(day, latitude, longitude, is_sunrise=True)
    sunset_h = calc_sun_time(day, latitude, longitude, is_sunrise=False)

    if sunrise_h is None or sunset_h is None:
        return 6 * 60, 18 * 60

    sunrise = max(0, min(int(round(sunrise_h * 60)), 24 * 60))
    sunset = max(0, min(int(round(sunset_h * 60)), 24 * 60))

    if sunset <= sunrise:
        return 6 * 60, 18 * 60
    return sunrise, sunset


def amap_geocode(address: str, city: str | None = None) -> tuple[float, float] | None:
    api_key = (get_setting_value("amap_api_key") or os.getenv("AMAP_API_KEY") or "").strip()
    if not api_key:
        return None

    params: dict[str, str] = {"key": api_key, "address": address}
    if city:
        params["city"] = city

    security_key = (get_setting_value("amap_security_key") or os.getenv("AMAP_SECURITY_KEY") or "").strip()
    if security_key:
        sorted_params = sorted(params.items(), key=lambda x: x[0])
        sign_raw = urlencode(sorted_params) + security_key
        params["sig"] = hashlib.md5(sign_raw.encode("utf-8")).hexdigest()

    url = f"{AMAP_API_URL}?{urlencode(params)}"

    try:
        with urlopen(url, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    if payload.get("status") != "1":
        return None

    geocodes = payload.get("geocodes") or []
    if not geocodes:
        return None

    location = geocodes[0].get("location")
    if not location or "," not in location:
        return None

    lng_s, lat_s = location.split(",", 1)
    lat = parse_float(lat_s)
    lon = parse_float(lng_s)
    if lat is None or lon is None or not is_valid_coords(lat, lon):
        return None
    return lat, lon


def resolve_coords_for_day(
    location_name: str,
    latitude: Any,
    longitude: Any,
    use_geocode: bool,
) -> tuple[float, float, str] | None:
    lat = parse_float(latitude)
    lon = parse_float(longitude)

    if lat is not None and lon is not None and is_valid_coords(lat, lon):
        return lat, lon, "manual"

    cleaned = (location_name or "").strip()
    if not cleaned:
        return None

    if use_geocode:
        geo = amap_geocode(cleaned)
        if geo:
            return geo[0], geo[1], "amap"

    return None


def build_sun_profile(days: list[str], location_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    profile: list[dict[str, Any]] = []
    last_known: tuple[float, float] | None = None

    for d in days:
        loc = location_map.get(d)
        if loc:
            lat = float(loc["latitude"])
            lon = float(loc["longitude"])
            last_known = (lat, lon)
            loc_name = loc["location_name"]
        else:
            if last_known is None:
                profile.append(
                    {
                        "date": d,
                        "location_name": "未设置(未提供坐标)",
                        "latitude": None,
                        "longitude": None,
                        "sunrise_minutes": 6 * 60,
                        "sunset_minutes": 18 * 60,
                        "sunrise": "06:00",
                        "sunset": "18:00",
                    }
                )
                continue
            lat, lon = last_known
            loc_name = "未设置(沿用上一日)"

        sunrise, sunset = calc_sunrise_sunset(date.fromisoformat(d), lat, lon)
        profile.append(
            {
                "date": d,
                "location_name": loc_name,
                "latitude": lat,
                "longitude": lon,
                "sunrise_minutes": sunrise,
                "sunset_minutes": sunset,
                "sunrise": f"{sunrise // 60:02d}:{sunrise % 60:02d}",
                "sunset": f"{sunset // 60:02d}:{sunset % 60:02d}",
            }
        )
    return profile


def get_current_user_id() -> int | None:
    user_id = session.get("user_id")
    if isinstance(user_id, int) and user_id > 0:
        return user_id
    return None


def current_user_row(db: sqlite3.Connection) -> sqlite3.Row | None:
    user_id = get_current_user_id()
    if not user_id:
        return None
    row = db.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        session.pop("user_id", None)
        return None
    return row


def api_login_required(func: Any) -> Any:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        db = get_db()
        user = current_user_row(db)
        if not user:
            return jsonify({"error": "请先登录"}), 401
        g.current_user_id = int(user["id"])
        g.current_username = str(user["username"])
        return func(*args, **kwargs)

    return wrapper


def api_admin_required(func: Any) -> Any:
    @api_login_required
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if str(getattr(g, "current_username", "")) != "u679c":
            return jsonify({"error": "仅管理员可操作"}), 403
        return func(*args, **kwargs)

    return wrapper


def issue_login_captcha() -> dict[str, Any]:
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    op = random.choice(["+", "-"])
    answer = a + b if op == "+" else a - b
    session["login_captcha_answer"] = answer
    return {"question": f"{a} {op} {b} = ?"}


def validate_login_captcha(raw_answer: Any) -> bool:
    expected = session.get("login_captcha_answer")
    session.pop("login_captcha_answer", None)
    if expected is None:
        return False
    try:
        return int(str(raw_answer).strip()) == int(expected)
    except (TypeError, ValueError):
        return False


def get_trip_or_404(db: sqlite3.Connection, trip_id: int, user_id: int) -> sqlite3.Row | None:
    return db.execute(
        """
        SELECT
            id,
            user_id,
            name,
            start_date,
            end_date,
            COALESCE(start_time, '00:00') AS start_time,
            COALESCE(end_time, '23:59') AS end_time
        FROM trips
        WHERE id = ? AND user_id = ?
        """,
        (trip_id, user_id),
    ).fetchone()


def get_item_or_404(db: sqlite3.Connection, item_id: int, user_id: int) -> sqlite3.Row | None:
    return db.execute(
        """
        SELECT
            i.id,
            i.trip_id,
            i.day_date,
            i.start_time,
            COALESCE(i.end_day_date, i.day_date) AS end_day_date,
            i.end_time,
            COALESCE(i.price, 0) AS price,
            COALESCE(i.annotation_offset, 0) AS annotation_offset,
            COALESCE(i.connector_length_adjust, 0) AS connector_length_adjust,
            COALESCE(i.annotation_side, 'auto') AS annotation_side,
            i.title,
            i.item_type,
            i.place,
            i.transport_mode,
            i.from_place,
            i.to_place,
            i.latitude,
            i.longitude,
            i.note
        FROM itinerary_items i
        JOIN trips t ON t.id = i.trip_id
        WHERE i.id = ? AND t.user_id = ?
        """,
        (item_id, user_id),
    ).fetchone()


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.get("/admin/<token>/users")
def admin_users_page(token: str) -> Any:
    db = get_db()
    user = current_user_row(db)
    if not user or str(user["username"]) != "u679c":
        return redirect("/")
    if token != get_admin_route_token():
        return redirect("/")
    return render_template("admin_users.html")


@app.get("/api/auth/me")
def auth_me() -> Any:
    db = get_db()
    user = current_user_row(db)
    if not user:
        return jsonify({"user": None})
    return jsonify({"user": {"id": int(user["id"]), "username": str(user["username"])}})


@app.get("/api/auth/captcha")
def auth_captcha() -> Any:
    return jsonify(issue_login_captcha())


@app.post("/api/auth/register")
def auth_register() -> Any:
    return jsonify({"error": "系统已关闭注册，请联系管理员创建账号"}), 403


@app.post("/api/auth/login")
def auth_login() -> Any:
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    captcha_answer = data.get("captcha_answer")
    if not username or not password:
        return jsonify({"error": "用户名和密码必填"}), 400
    if not validate_login_captcha(captcha_answer):
        return jsonify({"error": "验证码错误，请重试"}), 400

    db = get_db()
    user = db.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if not user or not check_password_hash(str(user["password_hash"]), password):
        return jsonify({"error": "用户名或密码错误"}), 401

    session["user_id"] = int(user["id"])
    return jsonify({"user": {"id": int(user["id"]), "username": str(user["username"])}})


@app.post("/api/auth/logout")
def auth_logout() -> Any:
    session.pop("user_id", None)
    return jsonify({"ok": True})


@app.get("/api/settings")
@api_login_required
def get_settings() -> Any:
    return jsonify(
        {
            "amap_api_key": get_setting_value("amap_api_key") or "",
            "amap_security_key": get_setting_value("amap_security_key") or "",
        }
    )


@app.put("/api/settings")
@api_login_required
def update_settings() -> Any:
    data = request.get_json(silent=True) or {}
    amap_api_key = (data.get("amap_api_key") or "").strip()
    amap_security_key = (data.get("amap_security_key") or "").strip()

    set_setting_value("amap_api_key", amap_api_key)
    set_setting_value("amap_security_key", amap_security_key)

    return jsonify({"ok": True})


@app.get("/api/admin/users")
@api_admin_required
def list_admin_users() -> Any:
    db = get_db()
    rows = db.execute(
        """
        SELECT
            u.id,
            u.username,
            COALESCE(u.is_locked, 0) AS is_locked,
            u.created_at,
            COUNT(t.id) AS trip_count
        FROM users u
        LEFT JOIN trips t ON t.user_id = u.id
        GROUP BY u.id, u.username, u.is_locked, u.created_at
        ORDER BY u.id ASC
        """
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.get("/api/admin/entry")
@api_admin_required
def get_admin_entry() -> Any:
    return jsonify({"path": f"/admin/{get_admin_route_token()}/users"})


@app.post("/api/admin/users")
@api_admin_required
def create_admin_user() -> Any:
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    is_locked = 1 if bool(data.get("is_locked", False)) else 0

    if len(username) < 3 or len(username) > 32:
        return jsonify({"error": "用户名长度需在 3~32 之间"}), 400
    if len(password) < 6:
        return jsonify({"error": "密码长度至少 6 位"}), 400

    db = get_db()
    existed = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existed:
        return jsonify({"error": "用户名已存在"}), 400

    cur = db.execute(
        "INSERT INTO users (username, password_hash, is_locked) VALUES (?, ?, ?)",
        (username, generate_password_hash(password), is_locked),
    )
    db.commit()

    row = db.execute(
        """
        SELECT id, username, COALESCE(is_locked, 0) AS is_locked, created_at
        FROM users
        WHERE id = ?
        """,
        (int(cur.lastrowid),),
    ).fetchone()
    return jsonify(dict(row)), 201


@app.put("/api/admin/users/<int:user_id>")
@api_admin_required
def update_admin_user(user_id: int) -> Any:
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    is_locked = 1 if bool(data.get("is_locked", False)) else 0

    if len(username) < 3 or len(username) > 32:
        return jsonify({"error": "用户名长度需在 3~32 之间"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not existing:
        return jsonify({"error": "用户不存在"}), 404

    dup = db.execute("SELECT id FROM users WHERE username = ? AND id <> ?", (username, user_id)).fetchone()
    if dup:
        return jsonify({"error": "用户名已存在"}), 400

    if password:
        if len(password) < 6:
            return jsonify({"error": "密码长度至少 6 位"}), 400
        db.execute(
            "UPDATE users SET username = ?, password_hash = ?, is_locked = ? WHERE id = ?",
            (username, generate_password_hash(password), is_locked, user_id),
        )
    else:
        db.execute("UPDATE users SET username = ?, is_locked = ? WHERE id = ?", (username, is_locked, user_id))
    db.commit()

    row = db.execute(
        """
        SELECT id, username, COALESCE(is_locked, 0) AS is_locked, created_at
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    ).fetchone()
    return jsonify(dict(row))


@app.delete("/api/admin/users/<int:user_id>")
@api_admin_required
def delete_admin_user(user_id: int) -> Any:
    if user_id == int(g.current_user_id):
        return jsonify({"error": "不能删除当前登录管理员账号"}), 400

    db = get_db()
    target = db.execute("SELECT id, COALESCE(is_locked, 0) AS is_locked FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        return jsonify({"error": "用户不存在"}), 404
    if int(target["is_locked"]) == 1:
        return jsonify({"error": "该用户已锁定，不可删除"}), 400

    cur = db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "用户不存在"}), 404
    return jsonify({"ok": True})


@app.get("/api/trips")
@api_login_required
def list_trips() -> Any:
    db = get_db()
    user_id = int(g.current_user_id)
    rows = db.execute(
        """
        SELECT
            id,
            name,
            start_date,
            end_date,
            COALESCE(start_time, '00:00') AS start_time,
            COALESCE(end_time, '23:59') AS end_time,
            created_at
        FROM trips
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (user_id,),
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/trips")
@api_login_required
def create_trip() -> Any:
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    start_date = data.get("start_date")
    end_date = data.get("end_date")
    start_time_raw = data.get("start_time")
    end_time_raw = data.get("end_time")

    if not name or not start_date or not end_date:
        return jsonify({"error": "name, start_date, end_date 必填"}), 400

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return jsonify({"error": "日期格式必须是 YYYY-MM-DD"}), 400

    if end < start:
        return jsonify({"error": "结束日期不能早于开始日期"}), 400

    parsed_start = parse_time_or_none(start_time_raw)
    parsed_end = parse_time_or_none(end_time_raw)
    if start_time_raw not in (None, "") and parsed_start is None:
        return jsonify({"error": "start_time 格式必须是 HH:MM"}), 400
    if end_time_raw not in (None, "") and parsed_end is None:
        return jsonify({"error": "end_time 格式必须是 HH:MM"}), 400

    start_time = parsed_start or datetime.strptime("00:00", "%H:%M").time()
    end_time = parsed_end or datetime.strptime("23:59", "%H:%M").time()

    start_dt = datetime.combine(start, start_time)
    end_dt = datetime.combine(end, end_time)
    if end_dt <= start_dt:
        return jsonify({"error": "行程结束时间必须晚于开始时间"}), 400

    db = get_db()
    user_id = int(g.current_user_id)
    cur = db.execute(
        """
        INSERT INTO trips (user_id, name, start_date, end_date, start_time, end_time)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, name, start_date, end_date, start_time.strftime("%H:%M"), end_time.strftime("%H:%M")),
    )
    db.commit()
    trip_id = cur.lastrowid
    row = db.execute(
        """
        SELECT
            id,
            name,
            start_date,
            end_date,
            COALESCE(start_time, '00:00') AS start_time,
            COALESCE(end_time, '23:59') AS end_time,
            created_at
        FROM trips
        WHERE id = ? AND user_id = ?
        """,
        (trip_id, user_id),
    ).fetchone()
    return jsonify(dict(row)), 201


@app.put("/api/trips/<int:trip_id>")
@api_login_required
def update_trip(trip_id: int) -> Any:
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    start_date = data.get("start_date")
    end_date = data.get("end_date")
    start_time_raw = data.get("start_time")
    end_time_raw = data.get("end_time")

    if not name or not start_date or not end_date:
        return jsonify({"error": "name, start_date, end_date 必填"}), 400

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return jsonify({"error": "日期格式必须是 YYYY-MM-DD"}), 400

    if end < start:
        return jsonify({"error": "结束日期不能早于开始日期"}), 400

    parsed_start = parse_time_or_none(start_time_raw)
    parsed_end = parse_time_or_none(end_time_raw)
    if start_time_raw not in (None, "") and parsed_start is None:
        return jsonify({"error": "start_time 格式必须是 HH:MM"}), 400
    if end_time_raw not in (None, "") and parsed_end is None:
        return jsonify({"error": "end_time 格式必须是 HH:MM"}), 400

    start_time = parsed_start or datetime.strptime("00:00", "%H:%M").time()
    end_time = parsed_end or datetime.strptime("23:59", "%H:%M").time()

    start_dt = datetime.combine(start, start_time)
    end_dt = datetime.combine(end, end_time)
    if end_dt <= start_dt:
        return jsonify({"error": "行程结束时间必须晚于开始时间"}), 400

    db = get_db()
    user_id = int(g.current_user_id)
    cur = db.execute(
        """
        UPDATE trips
        SET name = ?, start_date = ?, end_date = ?, start_time = ?, end_time = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            name,
            start_date,
            end_date,
            start_time.strftime("%H:%M"),
            end_time.strftime("%H:%M"),
            trip_id,
            user_id,
        ),
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "行程不存在"}), 404

    row = db.execute(
        """
        SELECT
            id,
            name,
            start_date,
            end_date,
            COALESCE(start_time, '00:00') AS start_time,
            COALESCE(end_time, '23:59') AS end_time,
            created_at
        FROM trips
        WHERE id = ? AND user_id = ?
        """,
        (trip_id, user_id),
    ).fetchone()
    return jsonify(dict(row))


@app.delete("/api/trips/<int:trip_id>")
@api_login_required
def delete_trip(trip_id: int) -> Any:
    db = get_db()
    user_id = int(g.current_user_id)
    cur = db.execute("DELETE FROM trips WHERE id = ? AND user_id = ?", (trip_id, user_id))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "行程不存在"}), 404
    return jsonify({"ok": True})


@app.get("/api/trips/<int:trip_id>/plan")
@api_login_required
def get_trip_plan(trip_id: int) -> Any:
    db = get_db()
    user_id = int(g.current_user_id)
    trip = get_trip_or_404(db, trip_id, user_id)
    if not trip:
        return jsonify({"error": "行程不存在"}), 404

    days = date_range(trip["start_date"], trip["end_date"])

    location_rows = db.execute(
        """
        SELECT trip_id, day_date, location_name, latitude, longitude, source, updated_at
        FROM trip_daily_locations
        WHERE trip_id = ?
        ORDER BY day_date
        """,
        (trip_id,),
    ).fetchall()
    location_map = {row["day_date"]: dict(row) for row in location_rows}

    items = db.execute(
        """
        SELECT
            id,
            trip_id,
            day_date,
            start_time,
            COALESCE(end_day_date, day_date) AS end_day_date,
            end_time,
            COALESCE(price, 0) AS price,
            COALESCE(annotation_offset, 0) AS annotation_offset,
            COALESCE(connector_length_adjust, 0) AS connector_length_adjust,
            COALESCE(annotation_side, 'auto') AS annotation_side,
            title,
            item_type,
            place,
            transport_mode,
            from_place,
            to_place,
            latitude,
            longitude,
            note
        FROM itinerary_items
        WHERE trip_id = ?
        ORDER BY day_date, start_time
        """,
        (trip_id,),
    ).fetchall()

    start_t = parse_time_or_none(trip["start_time"]) or datetime.strptime("00:00", "%H:%M").time()
    end_t = parse_time_or_none(trip["end_time"]) or datetime.strptime("23:59", "%H:%M").time()
    start_abs = time_to_minutes(start_t)
    end_abs = (len(days) - 1) * 1440 + time_to_minutes(end_t)
    if end_abs <= start_abs:
        end_abs = start_abs + 1

    return jsonify(
        {
            "trip": dict(trip),
            "days": days,
            "daily_locations": [dict(row) for row in location_rows],
            "sun_profile": build_sun_profile(days, location_map),
            "timeline_start_abs_minutes": start_abs,
            "timeline_end_abs_minutes": end_abs,
            "total_minutes": end_abs - start_abs,
            "items": [dict(row) for row in items],
        }
    )


@app.post("/api/trips/<int:trip_id>/daily-locations")
@api_login_required
def upsert_daily_location(trip_id: int) -> Any:
    data = request.get_json(silent=True) or {}
    day_date = data.get("day_date")
    location_name = (data.get("location_name") or "").strip()
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    use_geocode = bool(data.get("use_geocode", True))

    if not day_date or not location_name:
        return jsonify({"error": "day_date, location_name 必填"}), 400

    db = get_db()
    user_id = int(g.current_user_id)
    trip = get_trip_or_404(db, trip_id, user_id)
    if not trip:
        return jsonify({"error": "行程不存在"}), 404

    try:
        day_obj = date.fromisoformat(day_date)
    except ValueError:
        return jsonify({"error": "day_date 格式必须是 YYYY-MM-DD"}), 400

    trip_start = date.fromisoformat(trip["start_date"])
    trip_end = date.fromisoformat(trip["end_date"])
    if not (trip_start <= day_obj <= trip_end):
        return jsonify({"error": "day_date 必须在行程日期范围内"}), 400

    coords = resolve_coords_for_day(location_name, latitude, longitude, use_geocode)
    if not coords:
        return jsonify({"error": "无法解析经纬度。可手填 latitude/longitude，或配置 AMAP_API_KEY 后重试"}), 400

    lat, lon, source = coords

    db.execute(
        """
        INSERT INTO trip_daily_locations (trip_id, day_date, location_name, latitude, longitude, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(trip_id, day_date) DO UPDATE SET
            location_name = excluded.location_name,
            latitude = excluded.latitude,
            longitude = excluded.longitude,
            source = excluded.source,
            updated_at = CURRENT_TIMESTAMP
        """,
        (trip_id, day_date, location_name, lat, lon, source),
    )
    db.commit()

    row = db.execute(
        """
        SELECT trip_id, day_date, location_name, latitude, longitude, source, updated_at
        FROM trip_daily_locations
        WHERE trip_id = ? AND day_date = ?
        """,
        (trip_id, day_date),
    ).fetchone()
    return jsonify(dict(row)), 201


@app.delete("/api/trips/<int:trip_id>/daily-locations/<day_date>")
@api_login_required
def delete_daily_location(trip_id: int, day_date: str) -> Any:
    db = get_db()
    user_id = int(g.current_user_id)
    trip = get_trip_or_404(db, trip_id, user_id)
    if not trip:
        return jsonify({"error": "行程不存在"}), 404

    cur = db.execute(
        "DELETE FROM trip_daily_locations WHERE trip_id = ? AND day_date = ?",
        (trip_id, day_date),
    )
    db.commit()

    if cur.rowcount == 0:
        return jsonify({"error": "该日期地点不存在"}), 404
    return jsonify({"ok": True})


@app.post("/api/geocode")
@api_login_required
def geocode_address() -> Any:
    data = request.get_json(silent=True) or {}
    address = (data.get("address") or "").strip()
    if not address:
        return jsonify({"error": "address 必填"}), 400

    geo = amap_geocode(address)
    if not geo:
        return jsonify({"error": "高德解析失败，请检查 key 或地址"}), 400

    return jsonify({"latitude": geo[0], "longitude": geo[1]})


@app.post("/api/trips/<int:trip_id>/items")
@api_login_required
def add_item(trip_id: int) -> Any:
    data = request.get_json(silent=True) or {}
    start_datetime_raw = data.get("start_datetime")
    end_datetime_raw = data.get("end_datetime")
    day_date = data.get("day_date")
    end_day_date = data.get("end_day_date")
    start_time = data.get("start_time")
    end_time = data.get("end_time") or start_time
    item_type = (data.get("item_type") or "activity").strip()
    title = (data.get("title") or "").strip()
    place = (data.get("place") or "").strip()
    transport_mode = (data.get("transport_mode") or "").strip()
    from_place = (data.get("from_place") or "").strip()
    to_place = (data.get("to_place") or "").strip()
    price_raw = data.get("price")
    annotation_offset_raw = data.get("annotation_offset")
    connector_length_adjust_raw = data.get("connector_length_adjust")
    annotation_side_raw = data.get("annotation_side")
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    use_geocode = bool(data.get("use_geocode", True))
    note = (data.get("note") or "").strip()

    if not start_datetime_raw and (not day_date or not start_time):
        return jsonify({"error": "start_datetime 或 day_date+start_time 必填"}), 400

    if item_type not in ("activity", "transport", "address", "stay"):
        return jsonify({"error": "item_type 仅支持 activity/transport/address/stay"}), 400

    if item_type == "activity" and not title:
        return jsonify({"error": "活动类型必须填写 title"}), 400

    if item_type == "transport":
        if not transport_mode or not from_place or not to_place:
            return jsonify({"error": "交通类型必须填写 transport_mode, from_place, to_place"}), 400
        if not title:
            title = f"{transport_mode}: {from_place} → {to_place}"

    if item_type == "address":
        if not place:
            return jsonify({"error": "地址类型必须填写 place"}), 400
        if not title:
            title = f"地址: {place}"

    if item_type == "stay":
        if not place:
            return jsonify({"error": "住处类型必须填写 place"}), 400
        if not title:
            title = f"住处: {place}"

    price = 0.0
    if price_raw not in (None, ""):
        parsed_price = parse_float(price_raw)
        if parsed_price is None:
            return jsonify({"error": "price 必须是数字"}), 400
        price = parsed_price

    annotation_offset = 0.0
    if annotation_offset_raw not in (None, ""):
        parsed_annotation_offset = parse_float(annotation_offset_raw)
        if parsed_annotation_offset is None:
            return jsonify({"error": "annotation_offset 必须是数字"}), 400
        annotation_offset = parsed_annotation_offset

    connector_length_adjust = 0.0
    if connector_length_adjust_raw not in (None, ""):
        parsed_connector_length_adjust = parse_float(connector_length_adjust_raw)
        if parsed_connector_length_adjust is None:
            return jsonify({"error": "connector_length_adjust 必须是数字"}), 400
        connector_length_adjust = parsed_connector_length_adjust

    annotation_side = str(annotation_side_raw or "auto").strip().lower()
    if annotation_side not in ("auto", "above", "below"):
        return jsonify({"error": "annotation_side 仅支持 auto/above/below"}), 400

    db = get_db()
    user_id = int(g.current_user_id)
    trip = get_trip_or_404(db, trip_id, user_id)
    if not trip:
        return jsonify({"error": "行程不存在"}), 404

    start_dt = parse_local_datetime_or_none(start_datetime_raw)
    if start_datetime_raw not in (None, "") and start_dt is None:
        return jsonify({"error": "start_datetime 格式必须是 YYYY-MM-DDTHH:MM"}), 400
    if start_dt is None:
        try:
            event_date = date.fromisoformat(str(day_date))
            start_t = datetime.strptime(str(start_time), "%H:%M").time()
            start_dt = datetime.combine(event_date, start_t)
        except ValueError:
            return jsonify({"error": "日期或时间格式错误"}), 400

    end_dt = parse_local_datetime_or_none(end_datetime_raw)
    if end_datetime_raw not in (None, "") and end_dt is None:
        return jsonify({"error": "end_datetime 格式必须是 YYYY-MM-DDTHH:MM"}), 400
    if end_dt is None:
        if end_time:
            try:
                end_t = datetime.strptime(str(end_time), "%H:%M").time()
            except ValueError:
                return jsonify({"error": "日期或时间格式错误"}), 400
            if end_day_date not in (None, ""):
                try:
                    end_date_for_time = date.fromisoformat(str(end_day_date))
                except ValueError:
                    return jsonify({"error": "end_day_date 格式必须是 YYYY-MM-DD"}), 400
            else:
                end_date_for_time = start_dt.date()
                if end_t < start_dt.time() or (item_type in ("activity", "transport") and end_t == start_dt.time()):
                    end_date_for_time += timedelta(days=1)
            end_dt = datetime.combine(end_date_for_time, end_t)
        else:
            end_dt = start_dt

    if end_dt <= start_dt:
        return jsonify({"error": "结束时间必须晚于开始时间"}), 400

    trip_start_t = parse_time_or_none(trip["start_time"]) or datetime.strptime("00:00", "%H:%M").time()
    trip_end_t = parse_time_or_none(trip["end_time"]) or datetime.strptime("23:59", "%H:%M").time()
    trip_start_dt = datetime.combine(date.fromisoformat(trip["start_date"]), trip_start_t)
    trip_end_dt = datetime.combine(date.fromisoformat(trip["end_date"]), trip_end_t)

    if start_dt < trip_start_dt or end_dt > trip_end_dt:
        return jsonify({"error": "活动时间必须在行程开始/结束时间窗口内"}), 400

    event_date = start_dt.date().isoformat()
    end_day_date = end_dt.date().isoformat()
    start_time = start_dt.strftime("%H:%M")
    end_time = end_dt.strftime("%H:%M")

    lat: float | None = None
    lon: float | None = None
    if item_type == "address":
        parsed_lat = parse_float(latitude)
        parsed_lon = parse_float(longitude)
        if parsed_lat is not None and parsed_lon is not None:
            if not is_valid_coords(parsed_lat, parsed_lon):
                return jsonify({"error": "地址坐标范围不合法"}), 400
            lat, lon = parsed_lat, parsed_lon
        elif use_geocode:
            geo = amap_geocode(place)
            if geo:
                lat, lon = geo

    cur = db.execute(
        """
        INSERT INTO itinerary_items
        (trip_id, day_date, start_time, end_day_date, end_time, price, annotation_offset, connector_length_adjust, annotation_side, title, item_type, place, transport_mode, from_place, to_place, latitude, longitude, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trip_id,
            event_date,
            start_time,
            end_day_date,
            end_time,
            price,
            annotation_offset,
            connector_length_adjust,
            annotation_side,
            title,
            item_type,
            place,
            transport_mode if transport_mode else None,
            from_place if from_place else None,
            to_place if to_place else None,
            lat,
            lon,
            note,
        ),
    )
    db.commit()

    if item_type == "address" and lat is not None and lon is not None:
        db.execute(
            """
            INSERT INTO trip_daily_locations (trip_id, day_date, location_name, latitude, longitude, source, updated_at)
            VALUES (?, ?, ?, ?, ?, 'address', CURRENT_TIMESTAMP)
            ON CONFLICT(trip_id, day_date) DO UPDATE SET
                location_name = excluded.location_name,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (trip_id, event_date, place, lat, lon),
        )
        db.commit()

    row = db.execute(
        """
        SELECT
            id,
            trip_id,
            day_date,
            start_time,
            COALESCE(end_day_date, day_date) AS end_day_date,
            end_time,
            COALESCE(price, 0) AS price,
            COALESCE(annotation_offset, 0) AS annotation_offset,
            COALESCE(connector_length_adjust, 0) AS connector_length_adjust,
            COALESCE(annotation_side, 'auto') AS annotation_side,
            title,
            item_type,
            place,
            transport_mode,
            from_place,
            to_place,
            latitude,
            longitude,
            note
        FROM itinerary_items
        WHERE id = ?
        """,
        (cur.lastrowid,),
    ).fetchone()
    return jsonify(dict(row)), 201


@app.put("/api/items/<int:item_id>")
@api_login_required
def update_item(item_id: int) -> Any:
    data = request.get_json(silent=True) or {}
    db = get_db()
    user_id = int(g.current_user_id)
    existing = get_item_or_404(db, item_id, user_id)
    if not existing:
        return jsonify({"error": "活动不存在"}), 404

    item_type = (data.get("item_type") or existing["item_type"] or "").strip()
    if item_type != existing["item_type"]:
        return jsonify({"error": "不支持修改条目类型"}), 400

    start_datetime_raw = data.get("start_datetime")
    end_datetime_raw = data.get("end_datetime")
    day_date = data.get("day_date") or existing["day_date"]
    end_day_date = data.get("end_day_date") or existing["end_day_date"] or existing["day_date"]
    start_time = data.get("start_time") or existing["start_time"]
    end_time = data.get("end_time") or existing["end_time"] or start_time
    title = (data.get("title") if "title" in data else existing["title"] or "").strip()
    place = (data.get("place") if "place" in data else existing["place"] or "").strip()
    transport_mode = (data.get("transport_mode") if "transport_mode" in data else existing["transport_mode"] or "").strip()
    from_place = (data.get("from_place") if "from_place" in data else existing["from_place"] or "").strip()
    to_place = (data.get("to_place") if "to_place" in data else existing["to_place"] or "").strip()
    price_raw = data.get("price") if "price" in data else existing["price"]
    annotation_offset_raw = data.get("annotation_offset") if "annotation_offset" in data else existing["annotation_offset"]
    connector_length_adjust_raw = data.get("connector_length_adjust") if "connector_length_adjust" in data else existing["connector_length_adjust"]
    annotation_side_raw = data.get("annotation_side") if "annotation_side" in data else existing["annotation_side"]
    note = (data.get("note") if "note" in data else existing["note"] or "").strip()

    if not start_datetime_raw and (not day_date or not start_time):
        return jsonify({"error": "start_datetime 或 day_date+start_time 必填"}), 400

    if item_type == "activity" and not title:
        return jsonify({"error": "活动类型必须填写 title"}), 400

    if item_type == "transport":
        if not transport_mode or not from_place or not to_place:
            return jsonify({"error": "交通类型必须填写 transport_mode, from_place, to_place"}), 400
        if not title:
            title = f"{transport_mode}: {from_place} → {to_place}"

    if item_type == "address":
        if not place:
            return jsonify({"error": "地址类型必须填写 place"}), 400
        if not title:
            title = f"地址: {place}"

    if item_type == "stay":
        if not place:
            return jsonify({"error": "住处类型必须填写 place"}), 400
        if not title:
            title = f"住处: {place}"

    if price_raw in (None, ""):
        price = 0.0
    else:
        parsed_price = parse_float(price_raw)
        if parsed_price is None:
            return jsonify({"error": "price 必须是数字"}), 400
        price = parsed_price

    if annotation_offset_raw in (None, ""):
        annotation_offset = 0.0
    else:
        parsed_annotation_offset = parse_float(annotation_offset_raw)
        if parsed_annotation_offset is None:
            return jsonify({"error": "annotation_offset 必须是数字"}), 400
        annotation_offset = parsed_annotation_offset

    if connector_length_adjust_raw in (None, ""):
        connector_length_adjust = 0.0
    else:
        parsed_connector_length_adjust = parse_float(connector_length_adjust_raw)
        if parsed_connector_length_adjust is None:
            return jsonify({"error": "connector_length_adjust 必须是数字"}), 400
        connector_length_adjust = parsed_connector_length_adjust

    annotation_side = str(annotation_side_raw or "auto").strip().lower()
    if annotation_side not in ("auto", "above", "below"):
        return jsonify({"error": "annotation_side 仅支持 auto/above/below"}), 400

    trip = get_trip_or_404(db, int(existing["trip_id"]), user_id)
    if not trip:
        return jsonify({"error": "行程不存在"}), 404

    start_dt = parse_local_datetime_or_none(start_datetime_raw)
    if start_datetime_raw not in (None, "") and start_dt is None:
        return jsonify({"error": "start_datetime 格式必须是 YYYY-MM-DDTHH:MM"}), 400
    if start_dt is None:
        try:
            event_date = date.fromisoformat(str(day_date))
            start_t = datetime.strptime(str(start_time), "%H:%M").time()
            start_dt = datetime.combine(event_date, start_t)
        except ValueError:
            return jsonify({"error": "日期或时间格式错误"}), 400

    end_dt = parse_local_datetime_or_none(end_datetime_raw)
    if end_datetime_raw not in (None, "") and end_dt is None:
        return jsonify({"error": "end_datetime 格式必须是 YYYY-MM-DDTHH:MM"}), 400
    if end_dt is None:
        if end_time:
            try:
                end_t = datetime.strptime(str(end_time), "%H:%M").time()
            except ValueError:
                return jsonify({"error": "日期或时间格式错误"}), 400
            if end_day_date not in (None, ""):
                try:
                    end_date_for_time = date.fromisoformat(str(end_day_date))
                except ValueError:
                    return jsonify({"error": "end_day_date 格式必须是 YYYY-MM-DD"}), 400
            else:
                end_date_for_time = start_dt.date()
                if end_t < start_dt.time() or (item_type in ("activity", "transport") and end_t == start_dt.time()):
                    end_date_for_time += timedelta(days=1)
            end_dt = datetime.combine(end_date_for_time, end_t)
        else:
            end_dt = start_dt

    if end_dt <= start_dt:
        return jsonify({"error": "结束时间必须晚于开始时间"}), 400

    trip_start_t = parse_time_or_none(trip["start_time"]) or datetime.strptime("00:00", "%H:%M").time()
    trip_end_t = parse_time_or_none(trip["end_time"]) or datetime.strptime("23:59", "%H:%M").time()
    trip_start_dt = datetime.combine(date.fromisoformat(trip["start_date"]), trip_start_t)
    trip_end_dt = datetime.combine(date.fromisoformat(trip["end_date"]), trip_end_t)

    if start_dt < trip_start_dt or end_dt > trip_end_dt:
        return jsonify({"error": "活动时间必须在行程开始/结束时间窗口内"}), 400

    event_date = start_dt.date().isoformat()
    end_day_date = end_dt.date().isoformat()
    start_time = start_dt.strftime("%H:%M")
    end_time = end_dt.strftime("%H:%M")

    lat: float | None = None
    lon: float | None = None
    if item_type == "address":
        lat_src = data.get("latitude") if "latitude" in data else existing["latitude"]
        lon_src = data.get("longitude") if "longitude" in data else existing["longitude"]
        parsed_lat = parse_float(lat_src)
        parsed_lon = parse_float(lon_src)
        use_geocode = bool(data.get("use_geocode", True))

        if parsed_lat is not None and parsed_lon is not None:
            if not is_valid_coords(parsed_lat, parsed_lon):
                return jsonify({"error": "地址坐标范围不合法"}), 400
            lat, lon = parsed_lat, parsed_lon
        elif use_geocode:
            geo = amap_geocode(place)
            if geo:
                lat, lon = geo

    db.execute(
        """
        UPDATE itinerary_items
        SET day_date = ?, start_time = ?, end_day_date = ?, end_time = ?, price = ?, title = ?, item_type = ?,
            annotation_offset = ?, connector_length_adjust = ?, annotation_side = ?, place = ?, transport_mode = ?, from_place = ?, to_place = ?, latitude = ?, longitude = ?, note = ?
        WHERE id = ?
        """,
        (
            event_date,
            start_time,
            end_day_date,
            end_time,
            price,
            title,
            item_type,
            annotation_offset,
            connector_length_adjust,
            annotation_side,
            place if place else None,
            transport_mode if transport_mode else None,
            from_place if from_place else None,
            to_place if to_place else None,
            lat,
            lon,
            note if note else None,
            item_id,
        ),
    )
    db.commit()

    row = get_item_or_404(db, item_id, user_id)
    return jsonify(dict(row))


@app.delete("/api/items/<int:item_id>")
@api_login_required
def delete_item(item_id: int) -> Any:
    db = get_db()
    user_id = int(g.current_user_id)
    existing = get_item_or_404(db, item_id, user_id)
    if not existing:
        return jsonify({"error": "活动不存在"}), 404
    cur = db.execute("DELETE FROM itinerary_items WHERE id = ?", (item_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "活动不存在"}), 404
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
else:
    if not init_done:
        init_db()
        init_done = True
