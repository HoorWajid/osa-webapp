// app/static/js/viz_spo2.js
// SpO₂ Trend Visualization — Chart.js charts + metric tiles

document.addEventListener('DOMContentLoaded', function () {
    const features = JSON.parse(document.getElementById('featuresData').textContent);
    const analysis = JSON.parse(document.getElementById('analysisData').textContent);

    const C = {
        teal:      '#08979D',
        tealDark:  '#05585C',
        tealLight: '#6EC6CA',
        blue:      '#0a7fa6',
        purple:    '#8474A1',
        green:     '#198754',
        orange:    '#fd7e14',
        red:       '#dc3545',
        border:    '#d0ecee',
    };

    function v(key, dec = 2) {
        const val = features[key];
        if (val === undefined || val === null || isNaN(val)) return null;
        return parseFloat(parseFloat(val).toFixed(dec));
    }
    function fmt(val, unit = '', fallback = 'N/A') {
        return val === null ? fallback : val + (unit ? ' ' + unit : '');
    }

    function spo2Color(val) {
        if (val === null) return '#aaa';
        if (val >= 95) return C.green;
        if (val >= 90) return C.teal;
        if (val >= 88) return C.orange;
        if (val >= 85) return '#e67e22';
        return C.red;
    }

    function spo2Class(val) {
        if (val === null) return 'secondary';
        if (val >= 95) return 'success';
        if (val >= 90) return 'primary';
        if (val >= 88) return 'warning';
        return 'danger';
    }
    function spo2Label(val) {
        if (val === null) return 'No data';
        if (val >= 95) return 'Normal';
        if (val >= 90) return 'Mild Low';
        if (val >= 88) return 'Moderate';
        return 'Severe';
    }

    // ── Key metric tiles ─────────────────────────────────────────────────────
    const mean  = v('spo2_mean');
    const min   = v('spo2_min');
    const max   = v('spo2_max');
    const med   = v('spo2_median');
    const std   = v('spo2_std');
    const odi   = parseFloat((analysis.oxygen_desaturation || 0).toFixed(1));
    const t90   = v('time_below_90');
    const t88   = v('time_below_88');
    const hours = v('recording_hours', 1);

    const tilesContainer = document.getElementById('spo2Tiles');
    const tileItems = [
        { val: fmt(mean, '%'), label: 'Mean SpO₂', color: spo2Color(mean), extra: spo2Label(mean) },
        { val: fmt(min, '%'),  label: 'Min SpO₂',  color: spo2Color(min),  extra: spo2Label(min) },
        { val: fmt(max, '%'),  label: 'Max SpO₂',  color: C.green,         extra: 'Peak' },
        { val: fmt(med, '%'),  label: 'Median SpO₂', color: spo2Color(med), extra: spo2Label(med) },
        { val: fmt(std, '%'),  label: 'Std Dev',   color: C.teal,          extra: std !== null && std > 3 ? 'High Variability' : 'Stable' },
        { val: fmt(odi, '%'),  label: 'ODI',       color: odi > 20 ? C.red : odi > 5 ? C.orange : C.green, extra: odi > 20 ? 'High' : odi > 5 ? 'Moderate' : 'Normal' },
        { val: fmt(t90, '%'),  label: 'Time <90%', color: t90 !== null && t90 > 5 ? C.red : C.teal, extra: 'of recording' },
        { val: fmt(hours, 'h'), label: 'Recording', color: C.teal,         extra: 'Duration' },
    ];
    tilesContainer.innerHTML = tileItems.map(t => `
        <div class="col-6 col-sm-4 col-md-3 col-xl-auto" style="flex:1;min-width:120px;max-width:180px;">
            <div class="stat-tile">
                <div class="value" style="color:${t.color};">${t.val}</div>
                <div class="label">${t.label}</div>
                <small style="color:var(--text-muted);font-size:0.72rem;">${t.extra}</small>
            </div>
        </div>`
    ).join('');

    // ── Percentile chart ──────────────────────────────────────────────────────
    const pctLabels = ['P5', 'P10', 'P25', 'Median', 'P75', 'P90', 'P95'];
    const pctKeys   = ['spo2_p5', 'spo2_p10', 'spo2_p25', 'spo2_median', 'spo2_p75', 'spo2_p90', 'spo2_p95'];
    const pctValues = pctKeys.map(k => v(k));
    const hasPercentiles = pctValues.some(x => x !== null);

    const ctxPct = document.getElementById('chartPercentiles').getContext('2d');
    new Chart(ctxPct, {
        type: 'line',
        data: {
            labels: pctLabels,
            datasets: [{
                label: 'SpO₂ (%)',
                data: hasPercentiles ? pctValues : [91, 92, 93, 94, 96, 97, 98],
                fill: true,
                backgroundColor: C.teal + '22',
                borderColor: C.teal,
                borderWidth: 2.5,
                pointBackgroundColor: pctValues.map(x => spo2Color(x)),
                pointRadius: 6,
                pointHoverRadius: 9,
                tension: 0.4,
            }, {
                label: 'Normal Threshold (95%)',
                data: Array(7).fill(95),
                borderColor: C.green,
                borderWidth: 1.5,
                borderDash: [6, 4],
                pointRadius: 0,
                fill: false,
            }, {
                label: 'Hypoxemia Threshold (90%)',
                data: Array(7).fill(90),
                borderColor: C.red,
                borderWidth: 1.5,
                borderDash: [6, 4],
                pointRadius: 0,
                fill: false,
            }]
        },
        options: {
            responsive: true,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { position: 'top' },
                tooltip: {
                    callbacks: {
                        label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y !== null ? ctx.parsed.y.toFixed(1) + '%' : 'N/A'}`
                    }
                }
            },
            scales: {
                y: {
                    min: hasPercentiles ? Math.max(60, Math.min(...pctValues.filter(Boolean)) - 5) : 80,
                    max: 101,
                    grid: { color: '#eef7f7' },
                    ticks: { callback: v => v + '%' }
                },
                x: { grid: { display: false } }
            }
        }
    });

    // ── ODI gauge (doughnut) ──────────────────────────────────────────────────
    const ctxODI = document.getElementById('chartODI').getContext('2d');
    const odiNorm  = Math.min(odi, 100);
    const odiColor = odi > 20 ? C.red : odi > 10 ? C.orange : odi > 5 ? '#f0c040' : C.green;
    new Chart(ctxODI, {
        type: 'doughnut',
        data: {
            labels: ['Desaturated', 'Normal'],
            datasets: [{
                data: [odiNorm, Math.max(100 - odiNorm, 0)],
                backgroundColor: [odiColor, '#eef7f7'],
                borderWidth: 0,
                cutout: '70%'
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { label: ctx => ctx.label + ': ' + (ctx.label === 'Desaturated' ? odi + '%' : (100 - odi).toFixed(1) + '%') } }
            }
        },
        plugins: [{
            id: 'centerLabel',
            afterDraw(chart) {
                const { ctx, chartArea: { width, height, left, top } } = chart;
                ctx.save();
                ctx.font = 'bold 1.5rem Segoe UI, sans-serif';
                ctx.fillStyle = odiColor;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(odi + '%', left + width / 2, top + height / 2 - 8);
                ctx.font = '0.72rem Segoe UI, sans-serif';
                ctx.fillStyle = '#5a7a7a';
                ctx.fillText('ODI', left + width / 2, top + height / 2 + 14);
                ctx.restore();
            }
        }]
    });

    // ODI interpretation
    let odiTxt;
    if (odi <= 3)       odiTxt = `ODI of ${odi}% is normal. Very little time was spent with oxygen desaturation, indicating good overnight oxygenation.`;
    else if (odi <= 10) odiTxt = `ODI of ${odi}% is mildly elevated. Some periods of reduced oxygen saturation were detected, consistent with mild intermittent hypoxemia.`;
    else if (odi <= 20) odiTxt = `ODI of ${odi}% is moderately elevated. Significant desaturation time may contribute to cardiovascular and metabolic consequences.`;
    else                odiTxt = `ODI of ${odi}% is markedly elevated. This level of oxygen desaturation is concerning and consistent with moderate-to-severe OSA. Medical evaluation is recommended.`;
    document.getElementById('odiInterpretation').textContent = odiTxt;

    // ── Desaturation time bar chart ───────────────────────────────────────────
    const thresholds = ['<85%', '<88%', '<90%', '<93%', '<95%', '≥95%'];
    const threshKeys = ['time_below_85', 'time_below_88', 'time_below_90', 'time_below_93', 'time_below_95', 'time_above_95'];
    const threshVals = threshKeys.map(k => v(k, 1));
    const threshColors = [C.red, '#e67e22', C.orange, '#f0c040', C.tealLight, C.green];

    const ctxDesat = document.getElementById('chartDesatTime').getContext('2d');
    new Chart(ctxDesat, {
        type: 'bar',
        data: {
            labels: thresholds,
            datasets: [{
                label: '% of Recording',
                data: threshVals,
                backgroundColor: threshColors.map(c => c + 'cc'),
                borderColor: threshColors,
                borderWidth: 2,
                borderRadius: 8,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: { label: ctx => `${ctx.parsed.x !== null ? ctx.parsed.x.toFixed(1) : 'N/A'}% of recording time` }
                }
            },
            scales: {
                x: {
                    beginAtZero: true, max: 100,
                    grid: { color: '#eef7f7' },
                    ticks: { callback: v => v + '%' }
                },
                y: { grid: { display: false } }
            }
        }
    });

    // ── Zone distribution (doughnut) ─────────────────────────────────────────
    const t85  = v('time_below_85', 1)  || 0;
    const t88v = v('time_below_88', 1)  || 0;
    const t90v = v('time_below_90', 1)  || 0;
    const t95  = v('time_below_95', 1)  || 0;
    const tAbove95 = v('time_above_95', 1) || (100 - t95);

    const severe    = t85;
    const moderate  = Math.max(t88v - t85, 0);
    const mild      = Math.max(t90v - t88v, 0);
    const normal    = Math.max(100 - t90v, 0);

    const ctxZones = document.getElementById('chartZones').getContext('2d');
    new Chart(ctxZones, {
        type: 'doughnut',
        data: {
            labels: ['Normal (≥90%)', 'Mild Hypoxemia (88–90%)', 'Moderate (85–88%)', 'Severe (<85%)'],
            datasets: [{
                data: [Math.max(normal, 0), Math.max(mild, 0), Math.max(moderate, 0), Math.max(severe, 0)],
                backgroundColor: [C.green, '#f0c040', C.orange, C.red],
                borderWidth: 2,
                borderColor: '#fff',
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'bottom', labels: { font: { size: 11 } } },
                tooltip: { callbacks: { label: ctx => `${ctx.label}: ${ctx.parsed.toFixed(1)}%` } }
            }
        }
    });

    // Zones interpretation
    const warnText = severe > 5
        ? `Severe hypoxemia (<85%) accounts for ${severe.toFixed(1)}% of the recording — this is clinically significant and requires urgent evaluation.`
        : t90v > 20
        ? `${t90v.toFixed(1)}% of the recording was spent below 90% SpO₂ — moderate hypoxemic burden detected.`
        : t90v > 5
        ? `${t90v.toFixed(1)}% of recording was below 90% SpO₂, indicating mild-to-moderate intermittent hypoxemia.`
        : `The majority of the recording showed SpO₂ above 90%, suggesting adequate oxygenation.`;
    document.getElementById('zonesInterpretation').textContent = warnText;

    // ══════════════════════════════════════════════════════════════════════
    // WAVEFORM SECTION — fetch real SpO2 raw + clean signal from API
    // ══════════════════════════════════════════════════════════════════════
    const signalApi   = analysis.signal_api || null;
    const doctorView  = !!analysis.doctor_view;
    const waveSection = document.getElementById('spo2WaveformSection');
    const waveStatus  = document.getElementById('waveformStatus');

    async function buildWaveform() {
        if (!signalApi) {
            if (waveSection) waveSection.style.display = 'none';
            return;
        }
        if (waveStatus) waveStatus.textContent = 'Loading signal data…';

        let sig = null;
        try {
            const resp = await fetch(signalApi);
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            sig = await resp.json();
        } catch (e) {
            if (waveStatus) waveStatus.textContent = 'Signal data not available for this file.';
            return;
        }

        // Patient endpoint: sig.spo2 = clean only
        // Doctor/Admin endpoint: sig.spo2_raw + sig.spo2_clean
        const spo2Clean = doctorView ? (sig.spo2_clean || []) : (sig.spo2 || []);
        const spo2Raw   = doctorView ? (sig.spo2_raw   || []) : [];

        if (!spo2Clean.length && !spo2Raw.length) {
            if (waveStatus) waveStatus.textContent = 'No SpO₂ signal stored (CSV files do not include raw signal).';
            return;
        }

        if (waveStatus) waveStatus.style.display = 'none';

        // ── Build cleaning audit badge (doctor/admin only) ────────────────
        if (doctorView) {
            const auditEl = document.getElementById('spo2CleaningAudit');
            if (auditEl) {
                const cp = sig.spo2_cleaning_params || {};
                const invalidRemoved = sig.spo2_invalid_removed || 0;
                const validPct       = sig.spo2_valid_pct  != null ? sig.spo2_valid_pct.toFixed(1) : '—';
                auditEl.innerHTML =
                    `<div class="alert alert-secondary" style="font-size:0.8rem;border-radius:12px;">` +
                    `<strong><i class="bi bi-gear me-1"></i>SpO₂ Cleaning Pipeline:</strong><br>` +
                    `<span class="badge bg-light text-dark me-1">Stage 1: ${cp.stage1 || 'range clip [70,100]%'}</span>` +
                    `<span class="badge bg-light text-dark me-1">Stage 2: ${cp.stage2 || 'Savitzky-Golay smooth'}</span><br>` +
                    `<span class="text-muted">Raw samples: ${spo2Raw.length} &nbsp;·&nbsp; ` +
                    `Invalid removed: ${invalidRemoved} &nbsp;·&nbsp; ` +
                    `Valid data: ${validPct}% &nbsp;·&nbsp; ` +
                    `Clean samples: ${spo2Clean.length}</span>` +
                    `</div>`;
                auditEl.style.display = '';
            }
        }

        // ── Subsample for rendering (max 2000 points for performance) ────
        const MAX_RENDER = 2000;
        function subsample(arr) {
            if (!arr || !arr.length) return { vals: [], labels: [] };
            const step  = Math.max(1, Math.floor(arr.length / MAX_RENDER));
            const vals  = [];
            const labels = [];
            for (let i = 0; i < arr.length; i += step) {
                vals.push(arr[i]);
                // Label as minutes elapsed (1 Hz after downsampling)
                labels.push((i / 60).toFixed(1));
            }
            return { vals, labels };
        }

        const cleanSub = subsample(spo2Clean);
        const rawSub   = subsample(spo2Raw);
        const timeLabels = cleanSub.labels.length ? cleanSub.labels : rawSub.labels;

        // ── Waveform chart ────────────────────────────────────────────────
        const ctxWave = document.getElementById('chartSpo2Waveform');
        if (!ctxWave) return;

        const datasets = [];

        if (spo2Raw.length && doctorView) {
            datasets.push({
                label:           'SpO₂ Raw (unprocessed)',
                data:            rawSub.vals,
                borderColor:     C.red + 'aa',
                backgroundColor: 'transparent',
                borderWidth:     1,
                pointRadius:     0,
                tension:         0,
                order:           2,
            });
        }

        datasets.push({
            label:           doctorView ? 'SpO₂ Cleaned (clip + SG smooth)' : 'SpO₂',
            data:            cleanSub.vals,
            borderColor:     C.teal,
            backgroundColor: C.teal + '18',
            borderWidth:     doctorView ? 1.5 : 1.5,
            pointRadius:     0,
            tension:         0.15,
            fill:            true,
            order:           1,
        });

        // Threshold reference lines
        datasets.push({
            label:       '90% threshold',
            data:        new Array(timeLabels.length).fill(90),
            borderColor: C.orange + 'cc',
            borderWidth: 1,
            borderDash:  [6, 4],
            pointRadius: 0,
            fill:        false,
            order:       3,
        });
        datasets.push({
            label:       '95% normal lower limit',
            data:        new Array(timeLabels.length).fill(95),
            borderColor: C.green + '88',
            borderWidth: 1,
            borderDash:  [4, 4],
            pointRadius: 0,
            fill:        false,
            order:       4,
        });

        new Chart(ctxWave.getContext('2d'), {
            type: 'line',
            data: { labels: timeLabels, datasets },
            options: {
                responsive: true,
                animation:  false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { position: 'top', labels: { font: { size: 11 } } },
                    tooltip: {
                        callbacks: {
                            title: ctx => `t = ${ctx[0].label} min`,
                            label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toFixed(1) + ' %' : '—'}`,
                        }
                    }
                },
                scales: {
                    x: {
                        title: { display: true, text: 'Time (minutes)' },
                        ticks: { maxTicksLimit: 10 },
                        grid:  { color: '#eef7f7' }
                    },
                    y: {
                        title: { display: true, text: 'SpO₂ (%)' },
                        min: Math.max(60, Math.floor(spo2Clean.reduce((a, v) => v && v < a ? v : a, 100) - 5)),
                        max: 100,
                        grid: { color: '#eef7f7' }
                    }
                }
            }
        });
    }

    buildWaveform();
});