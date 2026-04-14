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

new window.Vue({
    el: '#app',
    delimiters: ['[[', ']]'],
    data() {
        return {
            trips: [],
            currentTripId: null,
            currentPlan: null,
            settings: {
                amap_api_key: '',
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
                    place: '',
                    note: '',
                },
                transport: {
                    transport_mode: '',
                    start_datetime: '',
                    end_datetime: '',
                    price: 0,
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
            return this.currentPlan ? sortItems(this.currentPlan.items) : [];
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
    },
    watch: {
        currentPlan() {
            this.$nextTick(() => this.renderTimeline());
        },
    },
    methods: {
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
        formatItemTimeRange(item) {
            const endDayDate = resolveEndDayDate(item);
            if (endDayDate === item.day_date) return `${item.day_date} ${item.start_time} - ${item.end_time}`;
            return `${item.day_date} ${item.start_time} - ${endDayDate} ${item.end_time}`;
        },
        buildItemLabel(item) {
            if (item.item_type === 'transport') {
                const mode = (item.transport_mode || item.title || '').trim();
                return `交通｜${mode}`;
            }
            if (item.item_type === 'stay') {
                const place = (item.place || item.title || '').trim();
                return `住处｜${place || '未命名'}`;
            }
            const title = (item.title || '').trim();
            const shortTitle = title.length > 4 ? `${title.slice(0, 4)}...` : title;
            return `活动｜${shortTitle || '未命名'}`;
        },
        itemDetailText(item) {
            let detail = this.formatItemTimeRange(item);
            if (item.item_type === 'transport') detail += ` | ${item.transport_mode || ''} ${item.from_place || ''} → ${item.to_place || ''}`;
            if (item.item_type === 'activity' && item.place) detail += ` | ${item.place}`;
            if (item.item_type === 'stay') detail += ` | ${item.place || '-'}`;
            if (item.item_type === 'address') {
                detail += ` | ${item.place || '-'}`;
                if (item.latitude !== null && item.longitude !== null) detail += ` | ${item.latitude}, ${item.longitude}`;
            }
            detail += ` | ¥${this.formatPrice(item.price)}`;
            if (item.note) detail += ` | ${item.note}`;
            return detail;
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
                    title: '',
                    note: '',
                };
            }
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
            try {
                this.settings = await api('/api/settings');
            } catch (_) {
                this.settings = { amap_api_key: '', amap_security_key: '' };
            }
        },
        openSettingsDialog() {
            this.loadSettings().then(() => {
                this.dialogs.settings = true;
            });
        },
        async submitSettings() {
            try {
                await api('/api/settings', {
                    method: 'PUT',
                    body: JSON.stringify(this.settings),
                });
                this.dialogs.settings = false;
                this.showSuccess('设置已保存');
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
            this.trips = await api('/api/trips');
            if (this.currentTripId && !this.trips.some((t) => t.id === this.currentTripId)) {
                this.currentTripId = null;
                this.currentPlan = null;
            }
            if (!this.currentTripId && this.trips.length > 0) {
                await this.loadTripPlan(this.trips[0].id);
            }
        },
        async loadTripPlan(tripId) {
            this.currentTripId = tripId;
            this.currentPlan = await api(`/api/trips/${tripId}/plan`);
        },
        itemAbsRange(item, days) {
            const startAbs = absMinutesFor(item.day_date, item.start_time, days);
            const endDayDate = resolveEndDayDate(item);
            const endAbs = absMinutesFor(endDayDate, item.end_time, days);
            if (startAbs === null || endAbs === null) return null;
            if (endAbs <= startAbs) return null;
            return { startAbs, endAbs };
        },
        renderTimeline() {
            const timelineTrack = document.getElementById('timeline-track');
            const timelineEvents = document.getElementById('timeline-events');
            const timelineDayLines = document.getElementById('timeline-day-lines');
            const timelineScale = document.getElementById('timeline-scale');
            const timelineDayBlocks = document.getElementById('timeline-day-blocks');
            const timelineDateBand = document.getElementById('timeline-date-band');
            if (!timelineTrack || !timelineEvents || !timelineDayLines || !timelineScale || !timelineDateBand || !timelineDayBlocks) return;

            timelineEvents.innerHTML = '';
            timelineScale.innerHTML = '';
            timelineDayLines.innerHTML = '';
            timelineDayBlocks.innerHTML = '';
            timelineDateBand.innerHTML = '';

            if (!this.currentPlan) return;

            const days = this.currentPlan.days;
            const windowStart = this.currentPlan.timeline_start_abs_minutes;
            const windowEnd = this.currentPlan.timeline_end_abs_minutes;
            const totalMinutes = this.currentPlan.total_minutes;
            const items = sortItems(this.currentPlan.items);

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

            const tickStep = 360; // 6 hours
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

            items.forEach((item) => {
                if (item.item_type === 'address') return;

                const absRange = this.itemAbsRange(item, days);
                if (!absRange) return;
                const { startAbs, endAbs } = absRange;

                const clipStart = Math.max(startAbs, windowStart);
                const clipEnd = Math.min(endAbs, windowEnd);
                if (clipEnd < clipStart) return;

                const left = ((clipStart - windowStart) / totalMinutes) * 100;
                const width = Math.max(((clipEnd - clipStart) / totalMinutes) * 100, 2.5);
                const center = left + width / 2;
                const clampedCenter = Math.max(7, Math.min(center, 93));

                const chip = document.createElement('div');
                chip.className = `timeline-event-chip chip-${item.item_type}`;
                chip.style.left = `${left}%`;
                chip.style.width = `${width}%`;
                timelineEvents.appendChild(chip);

                const labelText = this.buildItemLabel(item);

                let detail = this.formatItemTimeRange(item);
                if (item.item_type === 'transport') detail += ` <br/> ${item.transport_mode || ''} <br/> ${item.from_place || ''} → ${item.to_place || ''}`;
                if (item.item_type === 'activity' && item.place) detail += ` <br/> ${item.place}`;
                if (item.item_type === 'stay' && item.place) detail += ` <br/> ${item.place}`;
                detail += ` <br/> 价格：¥${this.formatPrice(item.price)}`;
                if (item.note) detail += ` <br/>备注： ${item.note}`;

                const annotation = document.createElement('div');
                const lane = timelineEvents.querySelectorAll('.timeline-annotation').length % 2 === 0 ? 'above' : 'below';
                annotation.className = `timeline-annotation ${lane}`;
                annotation.style.left = `${clampedCenter}%`;
                annotation.innerHTML = `
                    <div class="timeline-connector"></div>
                    <div class="timeline-label">${labelText}</div>
                    <div class="timeline-tooltip">${detail}</div>
                `;
                timelineEvents.appendChild(annotation);
            });
        },
    },
    mounted() {
        this.loadSettings()
            .then(() => this.loadTrips())
            .catch((err) => this.showError(err.message));
    },
});
