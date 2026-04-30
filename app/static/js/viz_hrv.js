// app/static/js/viz_hrv.js
// HRV Trend Visualization — Chart.js charts + metric tiles
// Lazy-initialises Frequency and Nonlinear tabs so Chart.js never
// measures a display:none canvas (avoids 0×0 blank-chart bug).

document.addEventListener('DOMContentLoaded', function () {
    const features = JSON.parse(document.getElementById('featuresData').textContent);
    const analysis = JSON.parse(document.getElementById('analysisData').textContent);

    const C = {
        teal:      '#08979D',
        tealDark:  '#05585C',
        tealLight: '#6EC6CA',
        purple:    '#8474A1',
        lavender:  '#CCA8D8',
        border:    '#d0ecee',
    };

    // ── Helpers ──────────────────────────────────────────────────────────────
    function v(key, decimals = 2) {
        const val = features[key];
        if (val === undefined || val === null) return null;
        const n = Number(val);
        if (!Number.isFinite(n)) return null;
        return parseFloat(n.toFixed(decimals));
    }
    function fmt(val, unit = '', fallback = 'N/A') {
        return val === null ? fallback : `${val}${unit ? ' ' + unit : ''}`;
    }
    function classify(metric, value) {
        if (value === null) return { cls: 'secondary', text: 'No data' };
        const ranges = {
            sdnn:       [{ lo: 0,   hi: 20,  cls: 'danger',  text: 'Very Low' },
                         { lo: 20,  hi: 50,  cls: 'warning', text: 'Low'      },
                         { lo: 50,  hi: 100, cls: 'success', text: 'Normal'   },
                         { lo: 100, hi: Infinity, cls: 'info', text: 'High'   }],
            rmssd:      [{ lo: 0,   hi: 15,  cls: 'danger',  text: 'Very Low' },
                         { lo: 15,  hi: 30,  cls: 'warning', text: 'Low'      },
                         { lo: 30,  hi: 80,  cls: 'success', text: 'Normal'   },
                         { lo: 80,  hi: Infinity, cls: 'info', text: 'High'   }],
            lf_hf_ratio:[{ lo: 0,   hi: 0.5, cls: 'info',    text: 'Parasym.' },
                         { lo: 0.5, hi: 2.5, cls: 'success', text: 'Balanced' },
                         { lo: 2.5, hi: 5,   cls: 'warning', text: 'Elevated' },
                         { lo: 5,   hi: Infinity, cls: 'danger', text: 'High' }],
        };
        const r = ranges[metric];
        if (!r) return { cls: 'primary', text: 'Measured' };
        for (const b of r) {
            if (value >= b.lo && value < b.hi) return { cls: b.cls, text: b.text };
        }
        return { cls: 'secondary', text: 'N/A' };
    }
    function tile(value, label, unit, badgeCls, badgeText) {
        return `<div class="col-6 col-sm-4 col-md-3 col-lg-2">
            <div class="stat-tile">
                <div class="value">${value === null ? '—' : value}</div>
                <div class="unit">${unit}</div>
                <div class="label">${label}</div>
                ${badgeCls ? `<span class="badge badge-range bg-${badgeCls}">${badgeText}</span>` : ''}
            </div>
        </div>`;
    }
    // Safe chart option defaults — critical for hidden-tab charts
    function chartDefaults(extra) {
        return Object.assign({
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 300 },
        }, extra);
    }

    // ── CSV notice ────────────────────────────────────────────────────────────
    if (analysis.file_type === 'csv') {
        const notice = document.getElementById('csvHrvNotice');
        if (notice) notice.style.display = '';
    }

    // ── Chart registry + tab-init flags ──────────────────────────────────────
    const charts = {};
    const tabInited = { freq: false, nonlinear: false };

    // ── Tab switching ─────────────────────────────────────────────────────────
    function switchTab(tab) {
        document.querySelectorAll('#hrvTabs .nav-link').forEach(l => l.classList.remove('active'));
        const activeLink = document.querySelector(`#hrvTabs [data-tab="${tab}"]`);
        if (activeLink) activeLink.classList.add('active');

        document.getElementById('tabTime').style.display      = tab === 'time'      ? '' : 'none';
        document.getElementById('tabFreq').style.display      = tab === 'freq'      ? '' : 'none';
        document.getElementById('tabNonlinear').style.display = tab === 'nonlinear' ? '' : 'none';

        // Lazy-init hidden tabs on first open
        if (tab === 'freq' && !tabInited.freq) {
            tabInited.freq = true;
            initFreqCharts();
        }
        if (tab === 'nonlinear' && !tabInited.nonlinear) {
            tabInited.nonlinear = true;
            initNonlinearCharts();
        }

        // Resize after browser paints the newly visible tab
        requestAnimationFrame(() => {
            if (tab === 'time') {
                charts.timeDomain?.resize();
                charts.rr?.resize();
            } else if (tab === 'freq') {
                charts.lfhf?.resize();
                charts.powerPie?.resize();
            } else if (tab === 'nonlinear') {
                charts.poincare?.resize();
                charts.complexity?.resize();
            }
        });
    }

    document.querySelectorAll('#hrvTabs .nav-link').forEach(link => {
        link.addEventListener('click', function (e) {
            e.preventDefault();
            switchTab(this.dataset.tab);
        });
    });

    // ════════════════════════════════════════════════════════════════════════
    // TIME DOMAIN — init immediately (tab is visible on load)
    // ════════════════════════════════════════════════════════════════════════
    const sdnn   = v('sdnn');
    const rmssd  = v('rmssd');
    const pnn50  = v('pnn50');
    const pnn20  = v('pnn20');
    const meanNN = v('mean_nni');
    const totalPeaks = v('total_peaks', 0);

    const timeTiles = document.getElementById('timeDomainTiles');
    if (timeTiles) {
        timeTiles.innerHTML =
            tile(fmt(sdnn,  'ms'), 'SDNN',    'ms',  ...Object.values(classify('sdnn', sdnn))) +
            tile(fmt(rmssd, 'ms'), 'RMSSD',   'ms',  ...Object.values(classify('rmssd', rmssd))) +
            tile(fmt(pnn50, '%'),  'pNN50',   '%',   null, null) +
            tile(fmt(pnn20, '%'),  'pNN20',   '%',   null, null) +
            tile(fmt(meanNN, 'ms'),'Mean NN', 'ms',  null, null) +
            tile(fmt(totalPeaks, ''), 'R-Peaks', 'count', null, null);
    }

    // Set canvas explicit height so maintainAspectRatio:false works correctly
    const tdCanvas = document.getElementById('chartTimeDomain');
    if (tdCanvas) tdCanvas.style.height = '240px';

    const ctxTime = tdCanvas?.getContext('2d');
    if (ctxTime) {
        charts.timeDomain = new Chart(ctxTime, {
            type: 'bar',
            data: {
                labels: ['SDNN (ms)', 'RMSSD (ms)', 'pNN50 (%)', 'pNN20 (%)'],
                datasets: [{
                    label: 'Measured',
                    data: [sdnn, rmssd, pnn50, pnn20],
                    backgroundColor: [C.teal + 'cc', C.tealLight + 'cc', C.purple + 'cc', C.lavender + 'cc'],
                    borderColor: [C.teal, C.tealLight, C.purple, C.lavender],
                    borderWidth: 2, borderRadius: 8,
                }, {
                    label: 'Normal Reference',
                    data: [50, 42, 18, 35],
                    backgroundColor: 'rgba(0,0,0,0.06)',
                    borderColor: 'rgba(0,0,0,0.15)',
                    borderWidth: 2, borderRadius: 8,
                }],
            },
            options: chartDefaults({
                plugins: { legend: { position: 'top' }, tooltip: { mode: 'index' } },
                scales: {
                    y: { beginAtZero: true, grid: { color: '#eef7f7' } },
                    x: { grid: { display: false } },
                },
            }),
        });
    }

    // Heart Rate doughnut
    const rrCanvas = document.getElementById('chartRR');
    if (rrCanvas) rrCanvas.style.height = '240px';

    const ctxRR = rrCanvas?.getContext('2d');
    if (ctxRR) {
        const rrVal = meanNN || 750;
        const hrBpm = rrVal > 0 ? Math.round(60000 / rrVal) : 0;
        charts.rr = new Chart(ctxRR, {
            type: 'doughnut',
            data: {
                labels: ['Mean RR', 'Remaining'],
                datasets: [{
                    data: [hrBpm, Math.max(200 - hrBpm, 0)],
                    backgroundColor: [C.teal, '#eef7f7'],
                    borderWidth: 0,
                    cutout: '72%',
                }],
            },
            options: chartDefaults({
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: ctx => ctx.label === 'Mean RR' ? `${hrBpm} bpm` : '' } },
                },
            }),
            plugins: [{
                id: 'centerLabel',
                afterDraw(chart) {
                    const { ctx, chartArea: { width, height, left, top } } = chart;
                    ctx.save();
                    ctx.font = `bold ${Math.min(width, height) * 0.18}px Segoe UI, sans-serif`;
                    ctx.fillStyle = C.tealDark;
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    const cx = left + width / 2, cy = top + height / 2;
                    ctx.fillText(hrBpm, cx, cy - 6);
                    ctx.font = `${Math.min(width, height) * 0.09}px Segoe UI, sans-serif`;
                    ctx.fillStyle = '#5a7a7a';
                    ctx.fillText('bpm', cx, cy + 14);
                    ctx.restore();
                },
            }],
        });
    }

    // Time domain interpretation
    let timeTxt = 'No HRV time-domain data available. This typically occurs with CSV files that do not include raw ECG signals.';
    if (sdnn !== null) {
        if (sdnn < 20)      timeTxt = `SDNN of ${sdnn} ms is very low, indicating severely reduced HRV. RMSSD of ${fmt(rmssd, 'ms')} confirms reduced parasympathetic activity.`;
        else if (sdnn < 50) timeTxt = `SDNN of ${sdnn} ms is below the normal range (50–100 ms), suggesting moderately reduced HRV, possibly related to OSA-induced autonomic dysfunction.`;
        else                timeTxt = `SDNN of ${sdnn} ms is within the healthy range. RMSSD of ${fmt(rmssd, 'ms')} reflects ${rmssd !== null && rmssd >= 30 ? 'adequate' : 'reduced'} parasympathetic tone.`;
    }
    const timeInterpEl = document.getElementById('timeDomainInterpretation');
    if (timeInterpEl) timeInterpEl.textContent = timeTxt;

    // ════════════════════════════════════════════════════════════════════════
    // FREQUENCY DOMAIN — lazy init (called when tab first opened)
    // ════════════════════════════════════════════════════════════════════════
    function initFreqCharts() {
        const lf    = v('lf');
        const hf    = v('hf');
        const vlf   = v('vlf');
        const ratio = v('lf_hf_ratio');
        const lfnu  = v('lfnu');
        const hfnu  = v('hfnu');

        const freqTiles = document.getElementById('freqDomainTiles');
        if (freqTiles) {
            freqTiles.innerHTML =
                tile(fmt(lf,    'ms²'), 'LF Power',   'ms²', null, null) +
                tile(fmt(hf,    'ms²'), 'HF Power',   'ms²', null, null) +
                tile(fmt(vlf,   'ms²'), 'VLF Power',  'ms²', null, null) +
                tile(fmt(ratio, ''),         'LF/HF Ratio', '',        ...Object.values(classify('lf_hf_ratio', ratio))) +
                tile(fmt(lfnu,  'nu'),       'LFnu',        'nu',       null, null) +
                tile(fmt(hfnu,  'nu'),       'HFnu',        'nu',       null, null);
        }

        const lfhfCanvas = document.getElementById('chartLFHF');
        if (lfhfCanvas) lfhfCanvas.style.height = '280px';
        const ctxLFHF = lfhfCanvas?.getContext('2d');
        if (ctxLFHF) {
            charts.lfhf = new Chart(ctxLFHF, {
                type: 'bar',
                data: {
                    labels: ['VLF', 'LF', 'HF', 'LF/HF ratio'],
                    datasets: [{
                        label: 'Power / Ratio',
                        data: [vlf, lf, hf, ratio],
                        backgroundColor: [C.tealLight + 'bb', C.teal + 'bb', C.purple + 'bb', C.lavender + 'bb'],
                        borderColor: [C.tealLight, C.teal, C.purple, C.lavender],
                        borderWidth: 2, borderRadius: 8,
                    }],
                },
                options: chartDefaults({
                    plugins: {
                        legend: { display: false },
                        tooltip: { callbacks: { label: ctx => ctx.parsed.y !== null ? ctx.parsed.y.toFixed(2) : 'N/A' } },
                    },
                    scales: {
                        y: { beginAtZero: true, grid: { color: '#eef7f7' } },
                        x: { grid: { display: false } },
                    },
                }),
            });
        }

        const pieCanvas = document.getElementById('chartPowerPie');
        if (pieCanvas) pieCanvas.style.height = '280px';
        const ctxPie = pieCanvas?.getContext('2d');
        if (ctxPie) {
            const hasFreq = lf !== null || hf !== null || vlf !== null;
            charts.powerPie = new Chart(ctxPie, {
                type: 'doughnut',
                data: {
                    labels: ['VLF', 'LF', 'HF'],
                    datasets: [{
                        data: hasFreq ? [vlf || 0, lf || 0, hf || 0] : [1, 1, 1],
                        backgroundColor: [C.tealLight, C.teal, C.purple],
                        borderWidth: 2, borderColor: '#fff',
                    }],
                },
                options: chartDefaults({
                    plugins: {
                        legend: { position: 'bottom' },
                        tooltip: {
                            callbacks: {
                                label: ctx => {
                                    if (!hasFreq) return 'No frequency data';
                                    const tot = (vlf || 0) + (lf || 0) + (hf || 0);
                                    const pct = tot > 0 ? ((ctx.parsed / tot) * 100).toFixed(1) : 0;
                                    return `${ctx.label}: ${ctx.parsed.toFixed(1)} ms² (${pct}%)`;
                                },
                            },
                        },
                    },
                }),
            });
        }

        // Freq interpretation
        let freqTxt = 'No frequency domain data available (requires EDF with ECG signal).';
        if (ratio !== null) {
            if      (ratio < 0.5)  freqTxt = `LF/HF of ${ratio} — parasympathetic dominance, reflecting adequate autonomic recovery.`;
            else if (ratio <= 2.5) freqTxt = `LF/HF of ${ratio} — balanced sympathovagal tone.`;
            else if (ratio <= 5)   freqTxt = `LF/HF of ${ratio} — elevated sympathetic dominance, commonly seen in OSA.`;
            else                   freqTxt = `LF/HF of ${ratio} — markedly elevated; frequently associated with moderate-to-severe OSA.`;
        }
        const freqEl = document.getElementById('freqDomainInterpretation');
        if (freqEl) freqEl.textContent = freqTxt;
    }

    // ════════════════════════════════════════════════════════════════════════
    // NONLINEAR — lazy init + async Poincaré (called when tab first opened)
    // ════════════════════════════════════════════════════════════════════════
    function initNonlinearCharts() {
        const sd1    = v('sd1');
        const sd2    = v('sd2');
        const csi    = v('csi');
        const cvi    = v('cvi');
        const sampen = v('sample_entropy');

        const nlTiles = document.getElementById('nonlinearTiles');
        if (nlTiles) {
            const ratio2 = (sd1 && sd2) ? parseFloat((sd2 / sd1).toFixed(2)) : null;
            nlTiles.innerHTML =
                tile(fmt(sd1,    'ms'), 'SD1 (short)', 'ms', null, null) +
                tile(fmt(sd2,    'ms'), 'SD2 (long)',  'ms', null, null) +
                tile(fmt(ratio2, ''),   'SD2/SD1',     '',   null, null) +
                tile(fmt(csi),          'CSI',         '',   null, null) +
                tile(fmt(cvi),          'CVI',         '',   null, null) +
                tile(fmt(sampen !== null ? parseFloat(sampen.toFixed(3)) : null, '', 'N/A'), 'SampEn', '', null, null);
        }

        // Complexity bar — synchronous, no fetch needed
        const compCanvas = document.getElementById('chartComplexity');
        if (compCanvas) compCanvas.style.height = '280px';
        const ctxComp = compCanvas?.getContext('2d');
        if (ctxComp) {
            const hasComp = csi !== null || cvi !== null || sampen !== null;
            if (hasComp) {
                charts.complexity = new Chart(ctxComp, {
                    type: 'bar',
                    data: {
                        labels: ['CSI', 'CVI', 'SampEn ×100'],
                        datasets: [{
                            label: 'Value',
                            data: [csi, cvi, sampen !== null ? parseFloat((sampen * 100).toFixed(2)) : null],
                            backgroundColor: [C.teal + 'cc', C.tealDark + 'cc', C.tealLight + 'cc'],
                            borderColor: [C.teal, C.tealDark, C.tealLight],
                            borderWidth: 2, borderRadius: 8,
                        }],
                    },
                    options: chartDefaults({
                        plugins: { legend: { display: false } },
                        scales: {
                            y: { beginAtZero: true, grid: { color: '#eef7f7' } },
                            x: { grid: { display: false } },
                        },
                    }),
                });
            } else {
                // No data — show message overlay
                _drawNoData(ctxComp, 'No complexity data (requires EDF with ECG signal)');
            }
        }

        // Nonlinear interpretation
        const nlIntEl = document.getElementById('nonlinearInterpretation');
        let nlTxt = '';
        if (sd1 !== null) {
            const r2 = (sd1 && sd2) ? (sd2 / sd1).toFixed(2) : null;
            nlTxt += `SD1=${fmt(sd1, 'ms')} — short-term variability (parasympathetic). SD2=${fmt(sd2, 'ms')} — long-term variability.`;
            if (r2) nlTxt += ` SD2/SD1=${r2} (${parseFloat(r2) > 2.5 ? 'sympathetic-dominant' : 'balanced'}).`;
        }
        if (sampen !== null) {
            nlTxt += ` SampEn=${sampen.toFixed(3)}: ${sampen < 1.0 ? 'reduced complexity — autonomic rigidity' : 'normal complexity'}.`;
        }
        if (!nlTxt) nlTxt = 'No nonlinear HRV data available (requires EDF with ECG signal).';
        if (nlIntEl) nlIntEl.textContent = nlTxt;

        // Poincaré — show loading state, then fetch
        const poincareCanvas = document.getElementById('chartPoincare');
        if (poincareCanvas) {
            poincareCanvas.style.height = '280px';
            _drawLoading(poincareCanvas.getContext('2d'), 'Loading Poincaré data…');
        }

        _buildPoincareAsync(sd1, sd2, sampen);
    }

    // Draw a centred "loading" message on a canvas
    function _drawLoading(ctx, msg) {
        const { width, height } = ctx.canvas.getBoundingClientRect();
        const w = width  || ctx.canvas.width  || 300;
        const h = height || ctx.canvas.height || 280;
        ctx.canvas.width  = w;
        ctx.canvas.height = h;
        ctx.clearRect(0, 0, w, h);
        ctx.fillStyle = '#f4fbfb';
        ctx.fillRect(0, 0, w, h);
        ctx.fillStyle = '#7A9E9C';
        ctx.font = '0.85rem Segoe UI, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(msg, w / 2, h / 2);
    }

    // Draw a centred "no data" message on a canvas context
    function _drawNoData(ctx, msg) {
        const w = ctx.canvas.offsetWidth  || 300;
        const h = ctx.canvas.offsetHeight || 280;
        ctx.clearRect(0, 0, w, h);
        ctx.fillStyle = '#f4fbfb';
        ctx.fillRect(0, 0, w, h);
        ctx.fillStyle = '#aac4c2';
        ctx.font = '0.85rem Segoe UI, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(msg, w / 2, h / 2);
    }

    // Async Poincaré: draw scalar fallback immediately, upgrade to scatter if API returns RR series
    async function _buildPoincareAsync(sd1, sd2) {
        const signalApi = analysis.signal_api || null;
        let rrClean = null, rrRaw = null;

        if (signalApi) {
            try {
                const resp = await fetch(signalApi);
                if (resp.ok) {
                    const sig = await resp.json();
                    rrClean = sig.rr_clean && sig.rr_clean.length > 2 ? sig.rr_clean : null;
                    rrRaw   = sig.rr_raw   && sig.rr_raw.length   > 2 ? sig.rr_raw   : null;

                    if (analysis.doctor_view && sig.has_data) {
                        const nlIntEl = document.getElementById('nonlinearInterpretation');
                        if (nlIntEl) {
                            const audit = `Cleaning: raw=${sig.rr_raw ? sig.rr_raw.length : '—'} beats, clean=${sig.rr_clean ? sig.rr_clean.length : '—'} beats, removed=${sig.artifacts_removed || 0}. `;
                            nlIntEl.textContent = audit + nlIntEl.textContent;
                        }
                    }
                }
            } catch (e) {
                console.warn('[HRV] Signal API unavailable, using scalar stats:', e);
            }
        }

        // Destroy loading placeholder if any
        if (charts.poincare) {
            charts.poincare.destroy();
            charts.poincare = null;
        }

        const poincareCanvas = document.getElementById('chartPoincare');
        if (!poincareCanvas) return;
        const ctxPoincare = poincareCanvas.getContext('2d');

        if (rrClean && rrClean.length > 2) {
            // Full scatter from RR series
            const MAX_PTS = 2000;
            const step = Math.max(1, Math.floor((rrClean.length - 1) / MAX_PTS));
            const cleanPts = [];
            for (let i = 0; i < rrClean.length - 1; i += step) {
                cleanPts.push({ x: rrClean[i], y: rrClean[i + 1] });
            }
            const datasets = [{
                label: 'RRⁿ vs RRⁿ₊₁ (clean)',
                data: cleanPts,
                backgroundColor: C.teal + '55',
                borderColor: C.teal + '99',
                pointRadius: 2.5,
                pointHoverRadius: 5,
            }];
            if (rrRaw && rrRaw.length > 2 && analysis.doctor_view) {
                const rawStep = Math.max(1, Math.floor((rrRaw.length - 1) / MAX_PTS));
                const rawPts = [];
                for (let i = 0; i < rrRaw.length - 1; i += rawStep) {
                    rawPts.push({ x: rrRaw[i], y: rrRaw[i + 1] });
                }
                datasets.unshift({
                    label: 'RRⁿ vs RRⁿ₊₁ (raw)',
                    data: rawPts,
                    backgroundColor: '#dc3545' + '33',
                    borderColor: '#dc3545' + '66',
                    pointRadius: 2,
                    pointHoverRadius: 4,
                    order: 1,
                });
            }
            charts.poincare = new Chart(ctxPoincare, {
                type: 'scatter',
                data: { datasets },
                options: chartDefaults({
                    animation: false,
                    plugins: {
                        legend: { position: 'top', labels: { font: { size: 11 } } },
                        tooltip: { callbacks: { label: ctx => `RR=${ctx.parsed.x.toFixed(1)} ms, RR+1=${ctx.parsed.y.toFixed(1)} ms` } },
                    },
                    scales: {
                        x: { title: { display: true, text: 'RRⁿ (ms)' }, grid: { color: '#eef7f7' } },
                        y: { title: { display: true, text: 'RRⁿ₊₁ (ms)' }, grid: { color: '#eef7f7' } },
                    },
                }),
            });
        } else {
            // Scalar fallback — SD1/SD2/SD2:SD1 bar chart
            const sd1v = sd1, sd2v = sd2;
            const ratio2 = (sd1v && sd2v) ? parseFloat((sd2v / sd1v).toFixed(2)) : null;
            const hasData = sd1v !== null || sd2v !== null;

            if (!hasData) {
                _drawNoData(ctxPoincare, 'No RR data available (EDF with ECG required)');
                return;
            }
            charts.poincare = new Chart(ctxPoincare, {
                type: 'bar',
                data: {
                    labels: ['SD1 (ms)', 'SD2 (ms)', 'SD2/SD1'],
                    datasets: [{
                        label: 'Poincaré metrics',
                        data: [sd1v, sd2v, ratio2],
                        backgroundColor: [C.teal + 'cc', C.tealDark + 'cc', C.tealLight + 'cc'],
                        borderColor: [C.teal, C.tealDark, C.tealLight],
                        borderWidth: 2, borderRadius: 8,
                    }],
                },
                options: chartDefaults({
                    plugins: {
                        legend: { display: false },
                        title: { display: true, text: 'Scalar SD1/SD2 (upload EDF for scatter)', font: { size: 11 }, color: '#888' },
                    },
                    scales: {
                        y: { beginAtZero: true, grid: { color: '#eef7f7' } },
                        x: { grid: { display: false } },
                    },
                }),
            });
        }

        // Resize after creation so hidden-tab dimensions are correct
        requestAnimationFrame(() => charts.poincare?.resize());
    }
});
