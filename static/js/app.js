import { api } from './api.js';
import { absMinutesFor, buildTimelineGradient, sortItems } from './timeline.js';

function formatLocalDateTime(dayDate, hhmm) {
    const time = (hhmm || '00:00').slice(0, 5);
    return `${dayDate}T${time}`;
}

function parseLocalDateTime(value) {
    const m = String(value || '').match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})$/);
    if (!m) return null;
    return { day_date: m[1], hhmm: m[2] };
}

function addMinutesToLocalDateTime(value, minutes) {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value;
    d.setMinutes(d.getMinutes() + minutes);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    const h = String(d.getHours()).padStart(2, '0');
    const min = String(d.getMinutes()).padStart(2, '0');
    return `${y}-${m}-${day}T${h}:${min}`;
}

function addDays(isoDate, days) {
    const d = new Date(`${isoDate}T00:00:00`);
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
}

function resolveEndDayDate(item) {
    if (item.end_day_date) return item.end_day_date;
    if ((item.item_type === 'activity' || item.item_type === 'transport') && item.end_time <= item.start_time) {
        return addDays(item.day_date, 1);
    }
    return item.day_date;
}

class TripItem {
    constructor(raw) {
        this.raw = raw || {};
        Object.assign(this, this.raw);
        this.price = Number(this.price) || 0;
        this.annotation_offset = Number(this.annotation_offset) || 0;
        this.connector_length_adjust = Number(this.connector_length_adjust) || 0;
        this.annotation_side = String(this.annotation_side || 'auto').toLowerCase();
        if (!['auto', 'above', 'below'].includes(this.annotation_side)) this.annotation_side = 'auto';
    }

    resolveEndDayDate() {
        return resolveEndDayDate(this);
    }

    getAbsRange(days) {
        const startAbs = absMinutesFor(this.day_date, this.start_time, days);
        const endAbs = absMinutesFor(this.resolveEndDayDate(), this.end_time, days);
        if (startAbs === null || endAbs === null || endAbs <= startAbs) return null;
        return { startAbs, endAbs };
    }

    formatTimeRange() {
        const endDayDate = this.resolveEndDayDate();
        if (endDayDate === this.day_date) return `${this.day_date} ${this.start_time} - ${this.end_time}`;
        return `${this.day_date} ${this.start_time} - ${endDayDate} ${this.end_time}`;
    }

    displayTitle() {
        if (this.item_type === 'transport') return this.title || this.transport_mode || '未命名';
        if (this.item_type === 'stay') return this.title || this.place || '未命名';
        return this.title || '未命名';
    }

    buildTimelineLabel() {
        if (this.item_type === 'transport') {
            const mode = (this.transport_mode || this.title || '').trim();
            return `交通｜${mode || '未命名'}`;
        }
        if (this.item_type === 'address') {
            const place = (this.place || this.title || '').trim();
            return `地址｜${place || '未命名'}`;
        }
        if (this.item_type === 'stay') {
            const place = (this.place || this.title || '').trim();
            return `住处｜${place || '未命名'}`;
        }
        const title = (this.title || '').trim();
        const shortTitle = title.length > 4 ? `${title.slice(0, 4)}...` : title;
        return `活动｜${shortTitle || '未命名'}`;
    }

    buildDetailText(priceFormatter) {
        const formatPrice = typeof priceFormatter === 'function' ? priceFormatter : (v) => Number(v || 0).toFixed(2);
        let detail = this.formatTimeRange();
        if (this.item_type === 'transport') detail += ` | ${this.transport_mode || ''} ${this.from_place || ''} → ${this.to_place || ''}`;
        if (this.item_type === 'activity' && this.place) detail += ` | ${this.place}`;
        if (this.item_type === 'stay') detail += ` | ${this.place || '-'}`;
        if (this.item_type === 'address') {
            detail += ` | ${this.place || '-'}`;
            if (this.latitude !== null && this.longitude !== null) detail += ` | ${this.latitude}, ${this.longitude}`;
        }
        if (this.item_type !== 'address') {
            detail += ` | ¥${formatPrice(this.price)}`;
        }
        if (this.note) detail += ` | ${this.note}`;
        return detail;
    }

    buildTimelineTooltipHtml(priceFormatter) {
        const formatPrice = typeof priceFormatter === 'function' ? priceFormatter : (v) => Number(v || 0).toFixed(2);
        let detail = this.formatTimeRange();
        if (this.item_type === 'transport') detail += ` <br/> ${this.transport_mode || ''} <br/> ${this.from_place || ''} → ${this.to_place || ''}`;
        if (this.item_type === 'address' && this.place) detail += ` <br/> ${this.place}`;
        if ((this.item_type === 'activity' || this.item_type === 'stay') && this.place) detail += ` <br/> ${this.place}`;
        detail += ` <br/> 价格：¥${formatPrice(this.price)}`;
        if (this.note) detail += ` <br/>备注： ${this.note}`;
        return detail;
    }
}

class TimelineNode {
    constructor({ item, left, width, clipStart, clipEnd, lane, orderIndex }) {
        this.item = item;
        this.left = left;
        this.width = width;
        this.clipStart = clipStart;
        this.clipEnd = clipEnd;
        this.lane = lane || 0;
        this.orderIndex = Number.isInteger(orderIndex) ? orderIndex : 0;

        const center = left + width / 2;
        this.baseCenter = Math.max(7, Math.min(center, 93));
        this.anchorCenter = this.baseCenter;
        this.side = 'above';
        this.level = 0;
    }

    laneCenterAffinity(laneCount) {
        const laneCenter = (laneCount - 1) / 2;
        const laneDistanceToCenter = Math.abs(this.lane - laneCenter);
        const maxDistance = Math.max(0.5, laneCenter);
        return Math.max(0, 1 - laneDistanceToCenter / maxDistance);
    }

    preferredSide(index, laneCount) {
        const laneCenter = (laneCount - 1) / 2;
        if (laneCount > 1) {
            if (this.lane < laneCenter) return 'above';
            if (this.lane > laneCenter) return 'below';
        }
        return index % 2 === 0 ? 'above' : 'below';
    }

    baseConnectorLength(laneCount) {
        return 12 + Math.round(this.laneCenterAffinity(laneCount) * 22);
    }
}

new window.Vue({
    el: '#app',
    delimiters: ['[[', ']]'],
    data() {
        return {
            viewportWidth: typeof window !== 'undefined' ? window.innerWidth : 1280,
            sidebarBreakpoint: 1200,
            sidebarAutoCollapsed: false,
            manualSidebarCollapsed: false,
            overlaySidebarOpen: false,
            timelineAxis: 'activity',
            timelineView: {
                zoom: 1,
                minZoom: 1,
                maxZoom: 12,
                centerRatio: 0.5,
            },
            timelineLeftPaddingMinutes: 180,
            timelineLayoutLock: {
                trackHeight: null,
                topMargin: null,
                bottomMargin: null,
            },
            timelineDocClickHandler: null,
            trips: [],
            currentTripId: null,
            currentPlan: null,
            auth: {
                user: null,
                loading: true,
                loginForm: {
                    username: '',
                    password: '',
                    captcha_answer: '',
                },
                captcha: {
                    question: '',
                },
            },
            settings: {
                amap_api_key: '',
                amap_api_key_new: '',
                amap_security_key: '',
            },
            dialogs: {
                activity: false,
                transport: false,
                stay: false,
                address: false,
                tripEdit: false,
                settings: false,
            },
            advancedOpen: {
                activity: [],
                transport: [],
                stay: [],
            },
            editingItem: null,
            tripForm: {
                name: '',
                start_date: '',
                end_date: '',
                start_time: '',
                end_time: '',
            },
            tripEditForm: {
                id: null,
                name: '',
                start_date: '',
                end_date: '',
                start_time: '',
                end_time: '',
            },
            itemForms: {
                activity: {
                    title: '',
                    start_datetime: '',
                    end_datetime: '',
                    price: 0,
                    annotation_offset: 0,
                    connector_length_adjust: 0,
                    annotation_side: 'auto',
                    place: '',
                    note: '',
                },
                transport: {
                    transport_mode: '',
                    start_datetime: '',
                    end_datetime: '',
                    price: 0,
                    annotation_offset: 0,
                    connector_length_adjust: 0,
                    annotation_side: 'auto',
                    from_place: '',
                    to_place: '',
                    title: '',
                    note: '',
                },
                stay: {
                    place: '',
                    start_datetime: '',
                    end_datetime: '',
                    price: 0,
                    annotation_offset: 0,
                    connector_length_adjust: 0,
                    annotation_side: 'auto',
                    title: '',
                    note: '',
                },
                address: {
                    place: '',
                    start_datetime: '',
                    end_datetime: '',
                    price: 0,
                    title: '',
                    note: '',
                    latitude: '',
                    longitude: '',
                    use_geocode: true,
                },
            },
        };
    },
    computed: {
        isAuthenticated() {
            return Boolean(this.auth.user);
        },
        isAdmin() {
            return Boolean(this.auth.user && this.auth.user.username === 'u679c');
        },
        isNarrowScreen() {
            return this.sidebarAutoCollapsed;
        },
        isCompactDialogScreen() {
            return this.viewportWidth <= 820;
        },
        dialogTop() {
            return this.isCompactDialogScreen ? '3vh' : '15vh';
        },
        dialogWideWidth() {
            return this.isCompactDialogScreen ? '96vw' : '760px';
        },
        dialogMediumWidth() {
            return this.isCompactDialogScreen ? '96vw' : '560px';
        },
        dialogNarrowWidth() {
            return this.isCompactDialogScreen ? '96vw' : '520px';
        },
        isSidebarCollapsed() {
            if (this.isNarrowScreen) return !this.overlaySidebarOpen;
            return this.manualSidebarCollapsed;
        },
        tripTitleText() {
            if (!this.currentPlan) return '';
            const t = this.currentPlan.trip;
            return `${t.name} (${t.start_date} ${t.start_time} ~ ${t.end_date} ${t.end_time})`;
        },
        sunMetaText() {
            if (!this.currentPlan) return '';
            const t = this.currentPlan.trip;
            return `${t.start_date} ${t.start_time} ~ ${t.end_date} ${t.end_time}`;
        },
        sortedItems() {
            if (!this.currentPlan) return [];
            return sortItems(this.currentPlan.items).map((item) => new TripItem(item));
        },
        listItems() {
            return this.sortedItems.filter((item) => item.item_type !== 'address');
        },
        addressItems() {
            return this.sortedItems.filter((item) => item.item_type === 'address');
        },
        totalBudget() {
            return this.sortedItems.reduce((sum, item) => sum + (Number(item.price) || 0), 0);
        },
        timelineSwitchTooltip() {
            return this.timelineAxis === 'activity' ? '切换为地址轴' : '切换为活动轴';
        },
        timelineZoomText() {
            return `${this.timelineView.zoom.toFixed(1)}x`;
        },
    },
    watch: {
        currentPlan() {
            this.resetTimelineView();
            this.resetTimelineLayoutLock();
            this.$nextTick(() => this.renderTimeline());
        },
        'auth.loading'(val) {
            if (!val) this.$nextTick(() => this.renderTimeline());
        },
    },
    methods: {
        getTimelineBaseRange() {
            if (!this.currentPlan) return null;
            const rawStart = Number(this.currentPlan.timeline_start_abs_minutes);
            const end = Number(this.currentPlan.timeline_end_abs_minutes);
            if (!Number.isFinite(rawStart) || !Number.isFinite(end) || end <= rawStart) return null;
            const leftPad = Math.max(0, Number(this.timelineLeftPaddingMinutes) || 0);
            const start = Math.max(0, rawStart - leftPad);
            if (end <= start) return null;
            return { start, end, total: end - start };
        },
        getTimelineViewRange() {
            const base = this.getTimelineBaseRange();
            if (!base) return null;
            const zoom = Math.min(this.timelineView.maxZoom, Math.max(this.timelineView.minZoom, Number(this.timelineView.zoom) || 1));
            const visible = Math.max(30, base.total / zoom);
            const movable = Math.max(0, base.total - visible);
            const centerRatio = Math.max(0, Math.min(1, Number(this.timelineView.centerRatio) || 0.5));
            const start = base.start + movable * centerRatio;
            const end = start + visible;
            return { start, end, total: visible, zoom };
        },
        setTimelineZoom(zoom, anchorRatio) {
            const base = this.getTimelineBaseRange();
            const current = this.getTimelineViewRange();
            if (!base || !current) return;
            const nextZoom = Math.min(this.timelineView.maxZoom, Math.max(this.timelineView.minZoom, zoom));
            if (!Number.isFinite(nextZoom)) return;
            const nextVisible = Math.max(30, base.total / nextZoom);
            const safeAnchor = Math.max(0, Math.min(1, Number(anchorRatio)));
            const anchorAbs = current.start + current.total * safeAnchor;
            const nextStartRaw = anchorAbs - nextVisible * safeAnchor;
            const minStart = base.start;
            const maxStart = base.end - nextVisible;
            const nextStart = Math.max(minStart, Math.min(maxStart, nextStartRaw));
            const movable = Math.max(1, base.total - nextVisible);
            this.timelineView.zoom = nextZoom;
            this.timelineView.centerRatio = (nextStart - base.start) / movable;
            this.$nextTick(() => this.renderTimeline());
        },
        zoomTimeline(factor) {
            const currentZoom = Number(this.timelineView.zoom) || 1;
            this.setTimelineZoom(currentZoom * factor, 0.5);
        },
        panTimelineByRatio(deltaRatio) {
            const view = this.getTimelineViewRange();
            const base = this.getTimelineBaseRange();
            if (!view || !base) return;
            const movable = Math.max(0, base.total - view.total);
            if (movable <= 0) return;
            const deltaMinutes = view.total * deltaRatio;
            const currentStart = base.start + movable * this.timelineView.centerRatio;
            const nextStart = Math.max(base.start, Math.min(base.end - view.total, currentStart + deltaMinutes));
            this.timelineView.centerRatio = (nextStart - base.start) / movable;
            this.$nextTick(() => this.renderTimeline());
        },
        panTimelineByPixels(deltaX, viewportWidth) {
            const view = this.getTimelineViewRange();
            const base = this.getTimelineBaseRange();
            const width = Number(viewportWidth) || 0;
            if (!view || !base || width <= 0) return;
            const movable = Math.max(0, base.total - view.total);
            if (movable <= 0) return;
            const deltaMinutes = (deltaX / width) * view.total;
            const currentStart = base.start + movable * this.timelineView.centerRatio;
            const nextStart = Math.max(base.start, Math.min(base.end - view.total, currentStart + deltaMinutes));
            this.timelineView.centerRatio = (nextStart - base.start) / movable;
            this.$nextTick(() => this.renderTimeline());
        },
        onTimelineWheel(evt) {
            if (!this.currentPlan) return;
            const slab = evt.currentTarget;
            const rect = slab && slab.getBoundingClientRect ? slab.getBoundingClientRect() : null;
            const width = rect && rect.width > 0 ? rect.width : 0;
            const anchorRatio = rect && rect.width > 0
                ? Math.max(0, Math.min(1, (evt.clientX - rect.left) / rect.width))
                : 0.5;

            // Trackpad pinch usually carries ctrlKey=true on wheel events.
            if (evt.ctrlKey) {
                const zoomFactor = Math.exp(-evt.deltaY * 0.0025);
                this.setTimelineZoom((Number(this.timelineView.zoom) || 1) * zoomFactor, anchorRatio);
                return;
            }

            if (Math.abs(evt.deltaX) > 0.5) {
                this.panTimelineByPixels(evt.deltaX, width);
                return;
            }

            if (evt.shiftKey) {
                this.panTimelineByPixels(evt.deltaY, width);
                return;
            }

            const zoomFactor = evt.deltaY > 0 ? 0.9 : 1.1;
            this.setTimelineZoom((Number(this.timelineView.zoom) || 1) * zoomFactor, anchorRatio);
        },
        resetTimelineView() {
            this.timelineView.zoom = 1;
            this.timelineView.centerRatio = 0.5;
        },
        resetTimelineLayoutLock() {
            this.timelineLayoutLock.trackHeight = null;
            this.timelineLayoutLock.topMargin = null;
            this.timelineLayoutLock.bottomMargin = null;
        },
        tripSelectionStorageKey() {
            const username = this.auth && this.auth.user ? this.auth.user.username : '';
            if (!username) return null;
            return `travel_plan_selected_trip_${username}`;
        },
        getSavedTripId() {
            const key = this.tripSelectionStorageKey();
            if (!key) return null;
            try {
                const raw = window.localStorage.getItem(key);
                if (!raw) return null;
                const id = Number(raw);
                return Number.isInteger(id) && id > 0 ? id : null;
            } catch (_) {
                return null;
            }
        },
        saveSelectedTripId(tripId) {
            const key = this.tripSelectionStorageKey();
            if (!key) return;
            try {
                window.localStorage.setItem(key, String(tripId));
            } catch (_) {
                // ignore
            }
        },
        async initAuth() {
            this.auth.loading = true;
            try {
                const data = await api('/api/auth/me');
                this.auth.user = data.user || null;
                if (this.auth.user) {
                    await this.loadSettings();
                    await this.loadTrips();
                } else {
                    await this.refreshCaptcha();
                }
            } catch (err) {
                this.showError(err.message);
            } finally {
                this.auth.loading = false;
            }
        },
        async refreshCaptcha() {
            try {
                const data = await api('/api/auth/captcha');
                this.auth.captcha.question = data.question || '';
                this.auth.loginForm.captcha_answer = '';
            } catch (err) {
                this.showError(err.message);
            }
        },
        async submitLogin() {
            try {
                const payload = { ...this.auth.loginForm };
                const data = await api('/api/auth/login', {
                    method: 'POST',
                    body: JSON.stringify(payload),
                });
                this.auth.user = data.user;
                this.auth.loginForm.password = '';
                this.auth.loginForm.captcha_answer = '';
                await this.loadSettings();
                await this.loadTrips();
                this.showSuccess('登录成功');
            } catch (err) {
                this.showError(err.message);
                await this.refreshCaptcha();
            }
        },
        async logout() {
            this.dialogs.settings = false;
            try {
                await api('/api/auth/logout', { method: 'POST' });
            } catch (_) {
                // ignore
            }
            this.auth.user = null;
            this.trips = [];
            this.currentTripId = null;
            this.currentPlan = null;
            this.settings = { amap_api_key: '', amap_api_key_new: '', amap_security_key: '' };
            await this.refreshCaptcha();
            this.showSuccess('已退出登录');
        },
        handleResize() {
            this.viewportWidth = window.innerWidth;
            this.sidebarAutoCollapsed = window.innerWidth < this.sidebarBreakpoint;
            if (!this.sidebarAutoCollapsed) {
                this.overlaySidebarOpen = false;
            }
        },
        toggleSidebar() {
            if (this.isNarrowScreen) {
                this.overlaySidebarOpen = !this.overlaySidebarOpen;
                return;
            }
            this.manualSidebarCollapsed = !this.manualSidebarCollapsed;
        },
        toggleTimelineAxis() {
            this.timelineAxis = this.timelineAxis === 'activity' ? 'address' : 'activity';
            this.resetTimelineLayoutLock();
            this.$nextTick(() => this.renderTimeline());
        },
        showError(message) {
            this.$message.error(message || '操作失败');
        },
        showSuccess(message) {
            this.$message.success(message);
        },
        formatPrice(value) {
            const num = Number(value);
            if (!Number.isFinite(num)) return '0.00';
            return num.toFixed(2);
        },
        itemTypeLabel(type) {
            if (type === 'transport') return '交通';
            if (type === 'activity') return '活动';
            if (type === 'stay') return '住处';
            if (type === 'address') return '地址';
            return '条目';
        },
        itemDetailText(item) {
            const model = item instanceof TripItem ? item : new TripItem(item);
            return model.buildDetailText((v) => this.formatPrice(v));
        },
        dialogTitle(type) {
            const prefix = this.editingItem && this.editingItem.item_type === type ? '编辑' : '新增';
            return `${prefix}${this.itemTypeLabel(type)}`;
        },
        defaultStartDateTime() {
            if (!this.currentPlan) return '';
            return formatLocalDateTime(this.currentPlan.trip.start_date, this.currentPlan.trip.start_time);
        },
        defaultEndDateTime() {
            const start = this.defaultStartDateTime();
            return addMinutesToLocalDateTime(start, 60);
        },
        resetItemForm(type) {
            const start = this.defaultStartDateTime();
            const end = this.defaultEndDateTime();
            if (type === 'activity') {
                this.itemForms.activity = {
                    title: '',
                    start_datetime: start,
                    end_datetime: end,
                    price: 0,
                    annotation_offset: 0,
                    connector_length_adjust: 0,
                    annotation_side: 'auto',
                    place: '',
                    note: '',
                };
            }
            if (type === 'transport') {
                this.itemForms.transport = {
                    transport_mode: '',
                    start_datetime: start,
                    end_datetime: end,
                    price: 0,
                    annotation_offset: 0,
                    connector_length_adjust: 0,
                    annotation_side: 'auto',
                    from_place: '',
                    to_place: '',
                    title: '',
                    note: '',
                };
            }
            if (type === 'address') {
                this.itemForms.address = {
                    place: '',
                    start_datetime: start,
                    end_datetime: end,
                    price: 0,
                    title: '',
                    note: '',
                    latitude: '',
                    longitude: '',
                    use_geocode: true,
                };
            }
            if (type === 'stay') {
                this.itemForms.stay = {
                    place: '',
                    start_datetime: start,
                    end_datetime: end,
                    price: 0,
                    annotation_offset: 0,
                    connector_length_adjust: 0,
                    annotation_side: 'auto',
                    title: '',
                    note: '',
                };
            }
            if (this.advancedOpen[type]) this.advancedOpen[type] = [];
        },
        fillItemFormForEdit(item) {
            const type = item.item_type;
            const form = { ...this.itemForms[type] };
            Object.keys(form).forEach((key) => {
                if (key === 'use_geocode') return;
                form[key] = item[key] ?? '';
            });
            form.start_datetime = formatLocalDateTime(item.day_date, item.start_time);
            form.end_datetime = formatLocalDateTime(resolveEndDayDate(item), item.end_time);
            if (type === 'address') form.use_geocode = true;
            this.itemForms[type] = form;
        },
        openItemDialog(type) {
            if (!this.currentPlan) return;
            this.editingItem = null;
            this.resetItemForm(type);
            this.dialogs[type] = true;
        },
        openEditDialog(item) {
            this.editingItem = item;
            this.resetItemForm(item.item_type);
            this.fillItemFormForEdit(item);
            this.dialogs[item.item_type] = true;
        },
        closeItemDialog(type) {
            this.dialogs[type] = false;
            this.editingItem = null;
            this.resetItemForm(type);
        },
        buildItemPayload(type) {
            const form = { ...this.itemForms[type] };
            const payload = { ...form, item_type: type };
            payload.price = Number(payload.price || 0);
            payload.annotation_offset = Number(payload.annotation_offset || 0);
            payload.connector_length_adjust = Number(payload.connector_length_adjust || 0);
            payload.annotation_side = String(payload.annotation_side || 'auto');

            if (payload.start_datetime) {
                const start = parseLocalDateTime(payload.start_datetime);
                if (!start) throw new Error('开始日期时间格式错误');
                payload.day_date = start.day_date;
                payload.start_time = start.hhmm;
            }

            if (payload.end_datetime) {
                const end = parseLocalDateTime(payload.end_datetime);
                if (!end) throw new Error('结束日期时间格式错误');
                payload.end_day_date = end.day_date;
                payload.end_time = end.hhmm;
            }

            delete payload.start_datetime;
            delete payload.end_datetime;

            if (type === 'address') {
                payload.use_geocode = Boolean(payload.use_geocode);
                if (!payload.end_time) payload.end_time = payload.start_time;
                if (!payload.latitude) delete payload.latitude;
                if (!payload.longitude) delete payload.longitude;
            }

            return payload;
        },
        async submitItem(type) {
            if (!this.currentTripId) return;
            try {
                const payload = this.buildItemPayload(type);
                const editing = this.editingItem && this.editingItem.item_type === type;
                await api(editing ? `/api/items/${this.editingItem.id}` : `/api/trips/${this.currentTripId}/items`, {
                    method: editing ? 'PUT' : 'POST',
                    body: JSON.stringify(payload),
                });
                this.closeItemDialog(type);
                await this.loadTripPlan(this.currentTripId);
                this.showSuccess('保存成功');
            } catch (err) {
                this.showError(err.message);
            }
        },
        async removeItem(item) {
            try {
                await this.$confirm('确定删除该条目吗？', '提示', {
                    type: 'warning',
                    confirmButtonText: '删除',
                    cancelButtonText: '取消',
                });
                await api(`/api/items/${item.id}`, { method: 'DELETE' });
                await this.loadTripPlan(this.currentTripId);
                this.showSuccess('删除成功');
            } catch (err) {
                if (err !== 'cancel') this.showError(err.message);
            }
        },
        async loadSettings() {
            if (!this.isAuthenticated) {
                this.settings = { amap_api_key: '', amap_api_key_new: '', amap_security_key: '' };
                return;
            }
            try {
                const data = await api('/api/settings');
                this.settings = {
                    amap_api_key: data.amap_api_key || '',
                    amap_api_key_new: '',
                    amap_security_key: data.amap_security_key || '',
                };
            } catch (_) {
                this.settings = { amap_api_key: '', amap_api_key_new: '', amap_security_key: '' };
            }
        },
        openSettingsDialog() {
            this.loadSettings().then(() => {
                this.dialogs.settings = true;
            });
        },
        async submitSettings() {
            try {
                const payload = {
                    amap_api_key: (this.settings.amap_api_key_new || '').trim() || this.settings.amap_api_key || '',
                    amap_security_key: this.settings.amap_security_key || '',
                };
                await api('/api/settings', {
                    method: 'PUT',
                    body: JSON.stringify(payload),
                });
                this.settings.amap_api_key = payload.amap_api_key;
                this.settings.amap_api_key_new = '';
                this.dialogs.settings = false;
                this.showSuccess('设置已保存');
            } catch (err) {
                this.showError(err.message);
            }
        },
        async openAdminUsers() {
            if (!this.isAdmin) return;
            try {
                const data = await api('/api/admin/entry');
                if (!data.path) throw new Error('管理员入口不可用');
                window.location.href = data.path;
            } catch (err) {
                this.showError(err.message);
            }
        },
        async submitTrip() {
            const payload = { ...this.tripForm };
            if (!payload.start_time) delete payload.start_time;
            if (!payload.end_time) delete payload.end_time;

            try {
                await api('/api/trips', { method: 'POST', body: JSON.stringify(payload) });
                this.tripForm = {
                    name: '',
                    start_date: '',
                    end_date: '',
                    start_time: '',
                    end_time: '',
                };
                await this.loadTrips();
                this.showSuccess('行程已创建');
            } catch (err) {
                this.showError(err.message);
            }
        },
        openTripEditDialog(trip) {
            this.tripEditForm = {
                id: trip.id,
                name: trip.name || '',
                start_date: trip.start_date || '',
                end_date: trip.end_date || '',
                start_time: trip.start_time || '',
                end_time: trip.end_time || '',
            };
            this.dialogs.tripEdit = true;
        },
        async submitTripEdit() {
            const tripId = this.tripEditForm.id;
            if (!tripId) return;
            try {
                await api(`/api/trips/${tripId}`, {
                    method: 'PUT',
                    body: JSON.stringify(this.tripEditForm),
                });
                this.dialogs.tripEdit = false;
                await this.loadTrips();
                await this.loadTripPlan(tripId);
                this.$message.warning('编辑后可能导致条目不在行程内，请妥善编辑');
            } catch (err) {
                this.showError(err.message);
            }
        },
        async deleteTrip() {
            if (!this.currentTripId) return;
            try {
                await this.$confirm('确定删除当前行程吗？', '提示', {
                    type: 'warning',
                    confirmButtonText: '删除',
                    cancelButtonText: '取消',
                });
                await api(`/api/trips/${this.currentTripId}`, { method: 'DELETE' });
                this.currentTripId = null;
                this.currentPlan = null;
                await this.loadTrips();
                this.showSuccess('行程已删除');
            } catch (err) {
                if (err !== 'cancel') this.showError(err.message);
            }
        },
        async loadTrips() {
            if (!this.isAuthenticated) {
                this.trips = [];
                this.currentTripId = null;
                this.currentPlan = null;
                return;
            }
            this.trips = await api('/api/trips');
            const exists = new Set(this.trips.map((t) => t.id));
            const savedTripId = this.getSavedTripId();

            let targetTripId = null;
            if (savedTripId && exists.has(savedTripId)) targetTripId = savedTripId;
            else if (this.currentTripId && exists.has(this.currentTripId)) targetTripId = this.currentTripId;
            else if (this.trips.length > 0) targetTripId = this.trips[0].id;

            if (!targetTripId) {
                this.currentTripId = null;
                this.currentPlan = null;
                return;
            }

            if (this.currentTripId !== targetTripId || !this.currentPlan) {
                await this.loadTripPlan(targetTripId);
            }
        },
        async loadTripPlan(tripId) {
            if (!this.isAuthenticated) return;
            this.currentTripId = tripId;
            this.currentPlan = await api(`/api/trips/${tripId}/plan`);
            this.saveSelectedTripId(tripId);
        },
        renderTimeline() {
            const timelineTrack = document.getElementById('timeline-track');
            const timelineEvents = document.getElementById('timeline-events');
            const timelineDayLines = document.getElementById('timeline-day-lines');
            const timelineScale = document.getElementById('timeline-scale');
            const timelineDayBlocks = document.getElementById('timeline-day-blocks');
            const timelineDateBand = document.getElementById('timeline-date-band');
            const timelineWrap = timelineTrack ? timelineTrack.closest('.timeline-wrap') : null;
            if (!timelineTrack || !timelineEvents || !timelineDayLines || !timelineScale || !timelineDateBand || !timelineDayBlocks) return;
            if (this.timelineDocClickHandler) {
                document.removeEventListener('click', this.timelineDocClickHandler, true);
                this.timelineDocClickHandler = null;
            }

            timelineEvents.innerHTML = '';
            timelineScale.innerHTML = '';
            timelineDayLines.innerHTML = '';
            timelineDayBlocks.innerHTML = '';
            timelineDateBand.innerHTML = '';
            if (timelineWrap) {
                timelineWrap.style.setProperty('--timeline-margin-top', '25px');
                timelineWrap.style.setProperty('--timeline-margin-bottom', '25px');
            }

            if (!this.currentPlan) return;

            const days = this.currentPlan.days;
            const viewRange = this.getTimelineViewRange();
            if (!viewRange) return;
            const windowStart = viewRange.start;
            const windowEnd = viewRange.end;
            const totalMinutes = viewRange.total;
            const items = this.sortedItems;

            timelineTrack.style.background = buildTimelineGradient(this.currentPlan.sun_profile, windowStart, windowEnd);

            for (let i = 1; i < days.length; i += 1) {
                const abs = i * 1440;
                if (abs <= windowStart || abs >= windowEnd) continue;
                const line = document.createElement('div');
                line.className = 'day-line';
                line.style.left = `${((abs - windowStart) / totalMinutes) * 100}%`;
                timelineDayLines.appendChild(line);
            }

            const weekNames = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
            for (let i = 0; i < days.length; i += 1) {
                const dayStart = i * 1440;
                const dayEnd = dayStart + 1440;
                const segStart = Math.max(dayStart, windowStart);
                const segEnd = Math.min(dayEnd, windowEnd);
                if (segEnd <= segStart) continue;

                const left = ((segStart - windowStart) / totalMinutes) * 100;
                const width = ((segEnd - segStart) / totalMinutes) * 100;
                const d = new Date(`${days[i]}T00:00:00`);
                const mmdd = days[i].slice(5);
                const label = `${mmdd} ${weekNames[d.getDay()]}`;

                const block = document.createElement('div');
                block.className = `timeline-day-block ${i % 2 === 0 ? 'odd' : 'even'}`;
                block.style.left = `${left}%`;
                block.style.width = `${width}%`;
                block.title = days[i];
                timelineDayBlocks.appendChild(block);

                const dateLabel = document.createElement('div');
                dateLabel.className = 'timeline-day-title';
                dateLabel.style.left = `${left}%`;
                dateLabel.style.width = `${width}%`;
                dateLabel.textContent = width < 10 ? mmdd : label;
                timelineDateBand.appendChild(dateLabel);
            }

            let tickStep = 360;
            if (totalMinutes <= 12 * 60) tickStep = 60;
            else if (totalMinutes <= 24 * 60) tickStep = 120;
            else if (totalMinutes <= 3 * 24 * 60) tickStep = 180;
            else if (totalMinutes <= 7 * 24 * 60) tickStep = 360;
            else tickStep = 720;
            const firstTick = Math.ceil(windowStart / tickStep) * tickStep;
            for (let abs = firstTick; abs <= windowEnd; abs += tickStep) {
                const left = ((abs - windowStart) / totalMinutes) * 100;
                if (left < 0 || left > 100) continue;

                const minsInDay = ((abs % 1440) + 1440) % 1440;
                const hh = String(Math.floor(minsInDay / 60)).padStart(2, '0');
                const mm = String(minsInDay % 60).padStart(2, '0');
                const tick = document.createElement('span');
                tick.className = `timeline-tick-label ${minsInDay === 0 ? 'midnight' : ''}`;
                tick.style.left = `${left}%`;
                tick.textContent = `${hh}:${mm}`;
                timelineScale.appendChild(tick);
            }

            const axisEntries = [];
            items.forEach((item) => {
                const inActivityAxis = item.item_type !== 'address';
                if ((this.timelineAxis === 'activity' && !inActivityAxis) || (this.timelineAxis === 'address' && inActivityAxis)) return;
                const absRange = item.getAbsRange(days);
                if (!absRange) return;
                const { startAbs, endAbs } = absRange;
                axisEntries.push({ item, startAbs, endAbs, lane: 0, orderIndex: 0 });
            });

            axisEntries.sort((a, b) => {
                if (a.startAbs !== b.startAbs) return a.startAbs - b.startAbs;
                if (a.endAbs !== b.endAbs) return a.endAbs - b.endAbs;
                return String(a.item.id || '').localeCompare(String(b.item.id || ''));
            });
            const laneEnds = [];
            axisEntries.forEach((entry, index) => {
                let assignedLane = -1;
                for (let i = 0; i < laneEnds.length; i += 1) {
                    if (entry.startAbs >= laneEnds[i]) {
                        assignedLane = i;
                        break;
                    }
                }
                if (assignedLane === -1) {
                    laneEnds.push(-Infinity);
                    assignedLane = laneEnds.length - 1;
                }
                entry.lane = assignedLane;
                entry.orderIndex = index;
                laneEnds[assignedLane] = Math.max(laneEnds[assignedLane], entry.endAbs);
            });

            const visibleItems = [];
            axisEntries.forEach((entry) => {
                const clipStart = Math.max(entry.startAbs, windowStart);
                const clipEnd = Math.min(entry.endAbs, windowEnd);
                if (clipEnd < clipStart) return;

                const left = ((clipStart - windowStart) / totalMinutes) * 100;
                const width = ((clipEnd - clipStart) / totalMinutes) * 100;
                visibleItems.push(new TimelineNode({
                    item: entry.item,
                    left,
                    width,
                    clipStart,
                    clipEnd,
                    lane: entry.lane,
                    orderIndex: entry.orderIndex,
                }));
            });

            const laneCount = Math.max(1, laneEnds.length);
            const chipHeight = 10;
            const laneGap = 4;
            const trackPaddingTop = 4;
            const trackPaddingBottom = 4;
            const flippedConnectorExtraFixedPx = -10;
            const computedTrackHeight = Math.max(
                20,
                trackPaddingTop + laneCount * chipHeight + (laneCount - 1) * laneGap + trackPaddingBottom,
            );
            const isBaseZoom = Math.abs((Number(this.timelineView.zoom) || 1) - 1) < 1e-6;
            const trackHeight = (!isBaseZoom && Number.isFinite(this.timelineLayoutLock.trackHeight))
                ? this.timelineLayoutLock.trackHeight
                : computedTrackHeight;
            const chipTopForLane = (lane) => {
                if (laneCount <= 1) return Math.round((trackHeight - chipHeight) / 2);
                return trackPaddingTop + lane * (chipHeight + laneGap);
            };
            timelineTrack.style.height = `${trackHeight}px`;
            const trackWidthPx = Math.max(1, timelineTrack.clientWidth || timelineTrack.getBoundingClientRect().width || 1);
            let maxAboveClearance = 0;
            let maxBelowClearance = 0;
            const placed = { above: [], below: [] };
            const rectOverlaps = (a, b) => !(a.x2 < b.x1 || a.x1 > b.x2 || a.y2 < b.y1 || a.y1 > b.y2);
            const labelPaddingPx = 12;
            const collisionGapPx = 6;
            const stepXPx = 8;
            const connectorStepPx = 8;
            const maxLevels = Math.max(12, visibleItems.length + 4);
            const toPct = (px) => (px / trackWidthPx) * 100;
            const minChipWidthPct = toPct(6);
            const stepXPct = toPct(stepXPx);
            const measureLabelSizePx = (labelText) => {
                const measurer = document.createElement('div');
                measurer.className = 'timeline-label';
                measurer.style.position = 'absolute';
                measurer.style.left = '-9999px';
                measurer.style.top = '-9999px';
                measurer.style.visibility = 'hidden';
                measurer.style.pointerEvents = 'none';
                measurer.textContent = labelText || '';
                timelineEvents.appendChild(measurer);
                const rect = measurer.getBoundingClientRect();
                timelineEvents.removeChild(measurer);
                const width = Math.max(84, Math.min(360, Math.ceil(rect.width || 84)));
                const height = Math.max(24, Math.ceil(rect.height || 24));
                return { width, height };
            };
            const buildAnchorCandidates = (node) => {
                const minCenter = Math.max(7, node.left);
                const maxCenter = Math.min(93, node.left + node.width);
                if (maxCenter <= minCenter || stepXPct <= 0) return [node.baseCenter];
                const candidates = [node.baseCenter];
                const maxStep = Math.ceil(Math.max(node.baseCenter - minCenter, maxCenter - node.baseCenter) / stepXPct);
                for (let i = 1; i <= maxStep; i += 1) {
                    const left = node.baseCenter - i * stepXPct;
                    const right = node.baseCenter + i * stepXPct;
                    if (left >= minCenter) candidates.push(left);
                    if (right <= maxCenter) candidates.push(right);
                }
                return candidates;
            };
            const tryPlaceOnSide = (node, side, labelWidthPct, labelHeightPx, baseConnector) => {
                const candidates = buildAnchorCandidates(node);
                for (let level = 0; level < maxLevels; level += 1) {
                    for (let i = 0; i < candidates.length; i += 1) {
                        const anchorCenter = candidates[i];
                        const connectorLength = baseConnector + level * connectorStepPx;
                        const yTop = side === 'above'
                            ? -(connectorLength + 4 + labelHeightPx)
                            : (connectorLength + 4);
                        const yBottom = side === 'above'
                            ? -(connectorLength + 4)
                            : (connectorLength + 4 + labelHeightPx);
                        const box = {
                            x1: anchorCenter - labelWidthPct / 2 - toPct(collisionGapPx),
                            x2: anchorCenter + labelWidthPct / 2 + toPct(collisionGapPx),
                            y1: yTop - collisionGapPx,
                            y2: yBottom + collisionGapPx,
                        };
                        const conflict = placed[side].some((p) => rectOverlaps(box, p));
                        if (!conflict) {
                            placed[side].push(box);
                            return { side, level, anchorCenter, connectorLength };
                        }
                    }
                }
                return null;
            };
            const laneLastPlacedSide = new Map();
            let activeClickKey = null;
            let activeClickResetter = null;

            visibleItems.forEach((entry, idx) => {
                const { item, left, width, lane } = entry;
                const itemKey = `${item.item_type}:${item.id}`;
                const chipTop = chipTopForLane(lane);
                const chipWidth = Math.min(Math.max(width, minChipWidthPct), Math.max(0.2, 100 - left));
                const chip = document.createElement('div');
                chip.className = `timeline-event-chip chip-${item.item_type}`;
                chip.style.left = `${left}%`;
                chip.style.width = `${chipWidth}%`;
                chip.style.top = `${chipTop}px`;
                chip.style.height = `${chipHeight}px`;
                timelineEvents.appendChild(chip);

                const labelText = item.buildTimelineLabel();
                const detail = item.buildTimelineTooltipHtml((v) => this.formatPrice(v));

                const annotation = document.createElement('div');
                let preferredSide;
                const autoSide = !['above', 'below'].includes(item.annotation_side);
                const stableIndex = Number.isInteger(entry.orderIndex) ? entry.orderIndex : idx;
                const naturalAutoSide = entry.preferredSide(stableIndex, laneCount);
                let laneAlternatedSide = false;
                if (['above', 'below'].includes(item.annotation_side)) {
                    preferredSide = item.annotation_side;
                } else {
                    const prevLaneSide = laneLastPlacedSide.get(lane);
                    if (prevLaneSide === 'above') {
                        preferredSide = 'below';
                        laneAlternatedSide = true;
                    } else if (prevLaneSide === 'below') {
                        preferredSide = 'above';
                        laneAlternatedSide = true;
                    } else {
                        preferredSide = naturalAutoSide;
                    }
                }
                const baseConnector = entry.baseConnectorLength(laneCount); // outer short, center long
                const labelSizePx = measureLabelSizePx(labelText);
                const labelWidthPct = toPct(labelSizePx.width + labelPaddingPx * 2);
                const placement = tryPlaceOnSide(entry, preferredSide, labelWidthPct, labelSizePx.height, baseConnector)
                    || tryPlaceOnSide(entry, preferredSide === 'above' ? 'below' : 'above', labelWidthPct, labelSizePx.height, baseConnector)
                    || { side: preferredSide, level: 0, anchorCenter: entry.baseCenter, connectorLength: baseConnector };
                laneLastPlacedSide.set(lane, placement.side);
                const connectorLengthAdjust = Number(item.connector_length_adjust || 0);
                const flippedFromNatural = autoSide && laneAlternatedSide && placement.side !== naturalAutoSide;
                const flippedConnectorCompensation = flippedFromNatural
                    ? laneCount * (chipHeight + laneGap) + flippedConnectorExtraFixedPx
                    : 0;
                const connectorLength = Math.max(
                    4,
                    (placement.connectorLength || (baseConnector + placement.level * connectorStepPx))
                        + flippedConnectorCompensation
                        + connectorLengthAdjust,
                );
                const annotationOffsetPct = toPct(Number(item.annotation_offset || 0));
                const anchorCenter = Math.max(0.5, Math.min(99.5, placement.anchorCenter + annotationOffsetPct));
                const labelClearance = connectorLength + 34;
                if (placement.side === 'above') maxAboveClearance = Math.max(maxAboveClearance, labelClearance);
                else maxBelowClearance = Math.max(maxBelowClearance, labelClearance);

                annotation.className = `timeline-annotation ${placement.side}`;
                annotation.style.left = `${anchorCenter}%`;
                annotation.style.top = `${chipTop + Math.round(chipHeight / 2)}px`;
                annotation.style.setProperty('--connector-length', `${connectorLength}px`);
                annotation.style.setProperty('--label-height', `${labelSizePx.height}px`);
                annotation.innerHTML = `
                    <div class="timeline-connector"></div>
                    <div class="timeline-label">${labelText}</div>
                    <div class="timeline-tooltip">${detail}</div>
                `;
                timelineEvents.appendChild(annotation);

                const setActiveState = (active) => {
                    chip.classList.toggle('is-active', active);
                    annotation.classList.toggle('is-active', active);
                };
                const handleLeave = (evt) => {
                    const related = evt.relatedTarget;
                    if (related && (chip.contains(related) || annotation.contains(related))) return;
                    if (activeClickKey === itemKey) return;
                    setActiveState(false);
                };
                const handleTimelineClick = (evt) => {
                    evt.preventDefault();
                    evt.stopPropagation();
                    if (activeClickKey === itemKey) {
                        this.openEditDialog(item);
                        return;
                    }
                    if (activeClickResetter && activeClickResetter !== setActiveState) {
                        activeClickResetter(false);
                    }
                    setActiveState(true);
                    activeClickKey = itemKey;
                    activeClickResetter = setActiveState;
                };
                chip.addEventListener('mouseenter', () => setActiveState(true));
                chip.addEventListener('mouseleave', handleLeave);
                chip.addEventListener('click', handleTimelineClick);
                annotation.addEventListener('mouseenter', () => setActiveState(true));
                annotation.addEventListener('mouseleave', handleLeave);
                annotation.addEventListener('click', handleTimelineClick);
            });

            this.timelineDocClickHandler = (evt) => {
                if (!timelineEvents.contains(evt.target)) {
                    if (activeClickResetter) activeClickResetter(false);
                    activeClickKey = null;
                    activeClickResetter = null;
                }
            };
            document.addEventListener('click', this.timelineDocClickHandler, true);

            if (timelineWrap) {
                let topMargin = Math.max(16, Math.ceil(maxAboveClearance + 2));
                let bottomMargin = Math.max(16, Math.ceil(maxBelowClearance + 2));
                if (isBaseZoom) {
                    this.timelineLayoutLock.trackHeight = computedTrackHeight;
                    this.timelineLayoutLock.topMargin = topMargin;
                    this.timelineLayoutLock.bottomMargin = bottomMargin;
                } else if (
                    Number.isFinite(this.timelineLayoutLock.topMargin)
                    && Number.isFinite(this.timelineLayoutLock.bottomMargin)
                ) {
                    topMargin = this.timelineLayoutLock.topMargin;
                    bottomMargin = this.timelineLayoutLock.bottomMargin;
                }
                timelineWrap.style.setProperty('--timeline-margin-top', `${topMargin}px`);
                timelineWrap.style.setProperty('--timeline-margin-bottom', `${bottomMargin}px`);
            }
        },
    },
    mounted() {
        this.handleResize();
        window.addEventListener('resize', this.handleResize);
        this.initAuth();
    },
    beforeDestroy() {
        if (this.timelineDocClickHandler) {
            document.removeEventListener('click', this.timelineDocClickHandler, true);
            this.timelineDocClickHandler = null;
        }
        window.removeEventListener('resize', this.handleResize);
    },
});
