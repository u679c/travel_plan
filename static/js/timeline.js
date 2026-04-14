export function toMinutes(t) {
    const [h, m] = String(t).split(':').map(Number);
    return h * 60 + m;
}

export function weekdayName(isoDate) {
    const day = new Date(`${isoDate}T00:00:00`).getDay();
    return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][day];
}

export function absMinutesFor(dayDate, hhmm, days) {
    const dayIdx = days.indexOf(dayDate);
    if (dayIdx < 0) return null;
    return dayIdx * 1440 + toMinutes(hhmm);
}

export function sortItems(items) {
    return [...items].sort((a, b) => {
        if (a.day_date !== b.day_date) return a.day_date.localeCompare(b.day_date);
        return a.start_time.localeCompare(b.start_time);
    });
}

export function buildTimelineGradient(sunProfile, windowStart, windowEnd) {
    if (!sunProfile || sunProfile.length === 0) return 'var(--blue-night)';
    const total = windowEnd - windowStart;
    if (total <= 0) return 'var(--blue-night)';

    const stops = [];
    for (let i = 0; i < sunProfile.length; i += 1) {
        const dayStart = i * 1440;
        const sunrise = dayStart + sunProfile[i].sunrise_minutes;
        const sunset = dayStart + sunProfile[i].sunset_minutes;
        const dawnStart = Math.max(dayStart, sunrise - 55);
        const dawnPeak = Math.max(dayStart, sunrise - 20);
        const duskPeak = Math.min(dayStart + 1440, sunset + 20);
        const duskEnd = Math.min(dayStart + 1440, sunset + 55);
        const dayMid = Math.max(sunrise, Math.min(sunset, Math.round((sunrise + sunset) / 2)));
        const segs = [
            [dayStart, 'var(--blue-night)'],
            [dawnStart, 'var(--night-soft)'],
            [dawnPeak, 'var(--dawn-light)'],
            [sunrise, 'var(--day-sky)'],
            [dayMid, 'var(--day-soft)'],
            [Math.max(sunrise + 1, sunset - 1), 'var(--day-sky)'],
            [sunset, 'var(--dusk-light)'],
            [duskPeak, 'var(--night-soft)'],
            [duskEnd, 'var(--blue-night)'],
            [dayStart + 1440, 'var(--blue-night)'],
        ];

        segs.forEach(([abs, color]) => {
            const clipped = Math.min(windowEnd, Math.max(windowStart, abs));
            const p = ((clipped - windowStart) / total) * 100;
            stops.push(`${color} ${p.toFixed(4)}%`);
        });
    }

    return `linear-gradient(to right, ${stops.join(', ')})`;
}
