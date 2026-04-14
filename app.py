from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from flask import Flask, g, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "travel_plan.db"
TZ_OFFSET_HOURS = 8
AMAP_API_URL = "https://restapi.amap.com/v3/geocode/geo"

app = Flask(__name__)
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
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            start_time TEXT NOT NULL DEFAULT '00:00',
            end_time TEXT NOT NULL DEFAULT '23:59',
            location_name TEXT,
            latitude REAL,
            longitude REAL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS itinerary_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            day_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0,
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


def get_trip_or_404(db: sqlite3.Connection, trip_id: int) -> sqlite3.Row | None:
    return db.execute(
        """
        SELECT
            id,
            name,
            start_date,
            end_date,
            COALESCE(start_time, '00:00') AS start_time,
            COALESCE(end_time, '23:59') AS end_time
        FROM trips
        WHERE id = ?
        """,
        (trip_id,),
    ).fetchone()


def get_item_or_404(db: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return db.execute(
        """
        SELECT
            id,
            trip_id,
            day_date,
            start_time,
            COALESCE(end_day_date, day_date) AS end_day_date,
            end_time,
            COALESCE(price, 0) AS price,
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
        (item_id,),
    ).fetchone()


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.get("/api/settings")
def get_settings() -> Any:
    return jsonify(
        {
            "amap_api_key": get_setting_value("amap_api_key") or "",
            "amap_security_key": get_setting_value("amap_security_key") or "",
        }
    )


@app.put("/api/settings")
def update_settings() -> Any:
    data = request.get_json(silent=True) or {}
    amap_api_key = (data.get("amap_api_key") or "").strip()
    amap_security_key = (data.get("amap_security_key") or "").strip()

    set_setting_value("amap_api_key", amap_api_key)
    set_setting_value("amap_security_key", amap_security_key)

    return jsonify({"ok": True})


@app.get("/api/trips")
def list_trips() -> Any:
    db = get_db()
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
        ORDER BY id DESC
        """
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/trips")
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
    cur = db.execute(
        """
        INSERT INTO trips (name, start_date, end_date, start_time, end_time)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, start_date, end_date, start_time.strftime("%H:%M"), end_time.strftime("%H:%M")),
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
        WHERE id = ?
        """,
        (trip_id,),
    ).fetchone()
    return jsonify(dict(row)), 201


@app.delete("/api/trips/<int:trip_id>")
def delete_trip(trip_id: int) -> Any:
    db = get_db()
    cur = db.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "行程不存在"}), 404
    return jsonify({"ok": True})


@app.get("/api/trips/<int:trip_id>/plan")
def get_trip_plan(trip_id: int) -> Any:
    db = get_db()
    trip = get_trip_or_404(db, trip_id)
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
    trip = get_trip_or_404(db, trip_id)
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
def delete_daily_location(trip_id: int, day_date: str) -> Any:
    db = get_db()
    trip = get_trip_or_404(db, trip_id)
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

    db = get_db()
    trip = get_trip_or_404(db, trip_id)
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
        (trip_id, day_date, start_time, end_day_date, end_time, price, title, item_type, place, transport_mode, from_place, to_place, latitude, longitude, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trip_id,
            event_date,
            start_time,
            end_day_date,
            end_time,
            price,
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
def update_item(item_id: int) -> Any:
    data = request.get_json(silent=True) or {}
    db = get_db()
    existing = get_item_or_404(db, item_id)
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

    trip = get_trip_or_404(db, int(existing["trip_id"]))
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
            place = ?, transport_mode = ?, from_place = ?, to_place = ?, latitude = ?, longitude = ?, note = ?
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

    row = get_item_or_404(db, item_id)
    return jsonify(dict(row))


@app.delete("/api/items/<int:item_id>")
def delete_item(item_id: int) -> Any:
    db = get_db()
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
