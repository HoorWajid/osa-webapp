// app/static/js/viz_hrv.js
// HRV Trend Visualization — Chart.js charts + metric tiles

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

    // ── Helpers ─────────────────────────────────────────────────────────────
    function v(key, decimals = 2) {
        const val = features[key];
        if (val === undefined || val === null || isNaN(val)) return null;
        return parseFloat(parseFloat(val).toFixed(decimals));
    }
    function fmt(val, unit = '', fallback = 'N/A') {
        return val === null ? fallback : val + (unit ? ' ' + unit : '');
    }
    function classify(metric, value) {
        if (value === null) return { cls: 'secondary', text: 'No data' };
        const ranges = {
            sdnn:       [{ lo: 0,  hi: 20,  cls: 'danger',  text: 'Very Low'  },
                         { lo: 20, hi: 50,  cls: 'warning', text: 'Low'       },
                         { lo: 50, hi: 100, cls: 'success', text: 'Normal'    },
                         { lo: 100,hi: 999, cls: 'info',    text: 'High'      }],
            rmssd:      [{ lo: 0,  hi: 15,  cls: 'danger',  text: 'Very Low'  },
                         { lo: 15, hi: 30,  cls: 'warning', text: 'Low'       },
                         { lo: 30, hi: 80,  cls: 'success', text: 'Normal'    },
                         { lo: 80, hi: 999, cls: 'info',    text: 'High'      }],
            lf_hf_ratio:[{ lo: 0,  hi: 0.5, cls: 'info',    text: 'Parasym.'  },
                         { lo: 0.5,hi: 2.5, cls: 'success', text: 'Balanced'  },
                         { lo: 2.5,hi: 5,   cls: 'warning', text: 'Elevated'  },
                         { lo: 5,  hi: 999, cls: 'danger',  text: 'High'      }],
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

    // ── Tab switching ────────────────────────────────────────────────────────
    document.querySelectorAll('#hrvTabs .nav-link').forEach(link => {
        link.addEventListener('click', function (e) {
            e.preventDefault();
            document.querySelectorAll('#hrvTabs .nav-link').forEach(l => l.classList.remove('active'));
            this.classList.add('active');
            const tab = this.dataset.tab;
            document.getElementById('tabTime').style.display      = tab === 'time'      ? '' : 'none';
            document.getElementById('tabFreq').style.display      = tab === 'freq'      ? '' : 'none';
            document.getElementById('tabNonlinear').style.display = tab === 'nonlinear' ? '' : 'none';
        });
    });

    // ════════════════════════════════════════════════════════════════════════
    // TIME DOMAIN
    // ════════════════════════════════════════════════════════════════════════
    const sdnn   = v('sdnn');
    const rmssd  = v('rmssd');
    const pnn50  = v('pnn50');
    const pnn20  = v('pnn20');
    const meanNN = v('mean_nni') || v('mean_rr');
    const medNN  = v('median_nni') || v('median_rr');

    const timeTiles = document.getElementById('timeDomainTiles');
    timeTiles.innerHTML =
        tile(fmt(sdnn, 'ms'), 'SDNN', 'ms', ...Object.values(classify('sdnn', sdnn))) +
        tile(fmt(rmssd, 'ms'), 'RMSSD', 'ms', ...Object.values(classify('rmssd', rmssd))) +
        tile(fmt(pnn50, '%'), 'pNN50', '%', null, null) +
        tile(fmt(pnn20, '%'), 'pNN20', '%', null, null) +
        tile(fmt(meanNN, 'ms'), 'Mean NN', 'ms', null, null) +
        tile(fmt(v('total_peaks'), ''), 'R-Peaks', 'count', null, null);

    // Time domain bar chart
    const ctxTime = document.getElementById('chartTimeDomain').getContext('2d');
    new Chart(ctxTime, {
        type: 'bar',
        data: {
            labels: ['SDNN (ms)', 'RMSSD (ms)', 'pNN50 (%)', 'pNN20 (%)'],
            datasets: [{
                label: 'Measured',
                data: [sdnn, rmssd, pnn50, pnn20],
                backgroundColor: [C.teal + 'cc', C.tealLight + 'cc', C.purple + 'cc', C.lavender + 'cc'],
                borderColor: [C.teal, C.tealLight, C.purple, C.lavender],
                borderWidth: 2,
                borderRadius: 8,
            }, {
                label: 'Normal Reference',
                data: [50, 42, 18, 35],
                backgroundColor: 'rgba(0,0,0,0.06)',
                borderColor: 'rgba(0,0,0,0.15)',
                borderWidth: 2,
                borderRadius: 8,
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { position: 'top' }, tooltip: { mode: 'index' } },
            scales: {
                y: { beginAtZero: true, grid: { color: '#eef7f7' } },
                x: { grid: { display: false } }
            }
        }
    });

    // RR intervals doughnut
    const ctxRR = document.getElementById('chartRR').getContext('2d');
    const rrVal = meanNN || 750;
    const hrBpm = rrVal > 0 ? Math.round(60000 / rrVal) : 0;
    new Chart(ctxRR, {
        type: 'doughnut',
        data: {
            labels: ['Mean RR', 'Remaining'],
            datasets: [{
                data: [hrBpm, Math.max(200 - hrBpm, 0)],
                backgroundColor: [C.teal, '#eef7f7'],
                borderWidth: 0,
                cutout: '72%'
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { label: ctx => ctx.label === 'Mean RR' ? `${hrBpm} bpm` : '' } }
            }
        },
        plugins: [{
            id: 'centerLabel',
            afterDraw(chart) {
                const { ctx, chartArea: { width, height, left, top } } = chart;
                ctx.save();
                ctx.font = 'bold 1.4rem Segoe UI, sans-serif';
                ctx.fillStyle = C.tealDark;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(hrBpm, left + width / 2, top + height / 2 - 6);
                ctx.font = '0.7rem Segoe UI, sans-serif';
                ctx.fillStyle = '#5a7a7a';
                ctx.fillText('bpm', left + width / 2, top + height / 2 + 14);
                ctx.restore();
            }
        }]
    });

    // Interpretation
    let timeTxt = 'No HRV time-domain data available. This typically occurs with CSV files that do not include raw ECG signals.';
    if (sdnn !== null) {
        if (sdnn < 20) timeTxt = `SDNN of ${sdnn} ms is very low, indicating severely reduced HRV which is associated with elevated cardiac risk and poor autonomic regulation. RMSSD of ${fmt(rmssd, 'ms')} confirms reduced parasympathetic activity.`;
        else if (sdnn < 50) timeTxt = `SDNN of ${sdnn} ms is below the normal range (50–100 ms), suggesting moderately reduced HRV. This may be related to OSA-induced autonomic dysfunction.`;
        else timeTxt = `SDNN of ${sdnn} ms is within the healthy range. RMSSD of ${fmt(rmssd, 'ms')} reflects ${rmssd !== null && rmssd >= 30 ? 'adequate' : 'reduced'} parasympathetic tone.`;
    }
    document.getElementById('timeDomainInterpretation').textContent = timeTxt;

    // ════════════════════════════════════════════════════════════════════════
    // FREQUENCY DOMAIN
    // ════════════════════════════════════════════════════════════════════════
    const lf     = v('lf');
    const hf     = v('hf');
    const vlf    = v('vlf');
    const ratio  = v('lf_hf_ratio');
    const lfnu   = v('lfnu');
    const hfnu   = v('hfnu');
    const tp     = v('total_power');

    const freqTiles = document.getElementById('freqDomainTiles');
    freqTiles.innerHTML =
        tile(fmt(lf, 'ms²'), 'LF Power', 'ms²', null, null) +
        tile(fmt(hf, 'ms²'), 'HF Power', 'ms²', null, null) +
        tile(fmt(vlf, 'ms²'), 'VLF Power', 'ms²', null, null) +
        tile(fmt(ratio, ''), 'LF/HF Ratio', '', ...Object.values(classify('lf_hf_ratio', ratio))) +
        tile(fmt(lfnu, 'nu'), 'LFnu', 'nu', null, null) +
        tile(fmt(hfnu, 'nu'), 'HFnu', 'nu', null, null);

    // LF/HF bar chart
    const ctxLFHF = document.getElementById('chartLFHF').getContext('2d');
    new Chart(ctxLFHF, {
        type: 'bar',
        data: {
            labels: ['VLF', 'LF', 'HF', 'LF/HF'],
            datasets: [{
                label: 'Power / Ratio',
                data: [vlf, lf, hf, ratio],
                backgroundColor: [
                    C.tealLight + 'bb', C.teal + 'bb', C.purple + 'bb', C.lavender + 'bb'
                ],
                borderColor: [C.tealLight, C.teal, C.purple, C.lavender],
                borderWidth: 2,
                borderRadius: 8,
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, grid: { color: '#eef7f7' } },
                x: { grid: { display: false } }
            }
        }
    });

    // Power pie
    const ctxPie = document.getElementById('chartPowerPie').getContext('2d');
    const hasFreq = lf !== null || hf !== null || vlf !== null;
    new Chart(ctxPie, {
        type: 'doughnut',
        data: {
            labels: ['VLF', 'LF', 'HF'],
            datasets: [{
                data: hasFreq ? [vlf || 0, lf || 0, hf || 0] : [1, 1, 1],
                backgroundColor: [C.tealLight, C.teal, C.purple],
                borderWidth: 2,
                borderColor: '#fff'
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'bottom' },
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            if (!hasFreq) return 'No data';
                            const tot = (vlf || 0) + (lf || 0) + (hf || 0);
                            const pct = tot > 0 ? ((ctx.parsed / tot) * 100).toFixed(1) : 0;
                            return `${ctx.label}: ${ctx.parsed.toFixed(1)} ms² (${pct}%)`;
                        }
                    }
                }
            }
        }
    });

    // Freq interpretation
    let freqTxt = 'No frequency domain data available.';
    if (ratio !== null) {
        if (ratio < 0.5) freqTxt = `LF/HF ratio of ${ratio} is low, indicating parasympathetic (vagal) dominance. This may reflect adequate autonomic recovery.`;
        else if (ratio <= 2.5) freqTxt = `LF/HF ratio of ${ratio} is balanced, suggesting normal sympathovagal balance.`;
        else if (ratio <= 5) freqTxt = `LF/HF ratio of ${ratio} is elevated, indicating sympathetic dominance. This is commonly seen in OSA patients due to repeated arousal and hypoxia events.`;
        else freqTxt = `LF/HF ratio of ${ratio} is markedly elevated, indicating strong sympathetic dominance. This level is frequently associated with moderate-to-severe OSA.`;
    }
    document.getElementById('freqDomainInterpretation').textContent = freqTxt;

    // ════════════════════════════════════════════════════════════════════════
    // NONLINEAR
    // ════════════════════════════════════════════════════════════════════════
    const sd1    = v('sd1');
    const sd2    = v('sd2');
    const csi    = v('csi');
    const cvi    = v('cvi');
    const sampen = v('sample_entropy') || v('sampen');

    const nlTiles = document.getElementById('nonlinearTiles');
    nlTiles.innerHTML =
        tile(fmt(sd1, 'ms'), 'SD1 (short)', 'ms', null, null) +
        tile(fmt(sd2, 'ms'), 'SD2 (long)', 'ms', null, null) +
        tile(fmt(sd2 && sd1 ? (sd2 / sd1).toFixed(2) : null), 'SD2/SD1', '', null, null) +
        tile(fmt(csi), 'CSI', '', null, null) +
        tile(fmt(cvi), 'CVI', '', null, null) +
        tile(fmt(sampen, '', 'N/A'), 'SampEn', '', null, null);

    // ── Nonlinear: fetch real RR series → true Poincaré scatter ────────────
    const signalApi = analysis.signal_api || null;

    async function buildNonlinearCharts() {
        let rrClean = null;
        let rrRaw   = null;  // only available for doctor_view

        if (signalApi) {
            try {
                const resp = await fetch(signalApi);
                if (resp.ok) {
                    const sig = await resp.json();
                    rrClean = sig.rr_clean && sig.rr_clean.length > 1 ? sig.rr_clean : null;
                    rrRaw   = sig.rr_raw   && sig.rr_raw.length   > 1 ? sig.rr_raw   : null;

                    // Doctor badge: show artifact stats if available
                    if (analysis.doctor_view && sig.has_data) {
                        const pct  = sig.artifact_pct || 0;
                        const el   = document.getElementById('nonlinearInterpretation');
                        const info = `Cleaning audit — Raw beats: ${sig.rr_raw ? sig.rr_raw.length : '—'} | Clean beats: ${sig.rr_clean ? sig.rr_clean.length : '—'} | Artifacts removed: ${sig.artifacts_removed || 0} (${pct}%). Method: ${(sig.cleaning_params || {}).method || 'N/A'}. `;
                        el.textContent = info;
                    }
                }
            } catch (e) {
                console.warn('Signal API unavailable, falling back to scalar stats:', e);
            }
        }

        // ── True Poincaré scatter plot from RR series ──────────────────────
        const ctxPoincare = document.getElementById('chartPoincare').getContext('2d');

        if (rrClean && rrClean.length > 2) {
            // Build RRn vs RRn+1 scatter
            const rr = rrClean;
            const pts = [];
            const MAX_PTS = 2000;  // cap scatter points for performance
            const step   = Math.max(1, Math.floor((rr.length - 1) / MAX_PTS));
            for (let i = 0; i < rr.length - 1; i += step) {
                pts.push({ x: rr[i], y: rr[i + 1] });
            }

            // Compute ellipse axes from SD1/SD2 (or recalculate)
            const plotSd1 = sd1 || null;
            const plotSd2 = sd2 || null;

            const datasets = [{
                label: 'RRₙ vs RRₙ₊₁ (clean)',
                data: pts,
                backgroundColor: C.teal + '55',
                borderColor: C.teal + '99',
                pointRadius: 2.5,
                pointHoverRadius: 5,
            }];

            // If doctor view and raw available, overlay raw in background
            if (rrRaw && rrRaw.length > 2) {
                const rawPts = [];
                const rawStep = Math.max(1, Math.floor((rrRaw.length - 1) / MAX_PTS));
                for (let i = 0; i < rrRaw.length - 1; i += rawStep) {
                    rawPts.push({ x: rrRaw[i], y: rrRaw[i + 1] });
                }
                datasets.unshift({
                    label: 'RRₙ vs RRₙ₊₁ (raw)',
                    data: rawPts,
                    backgroundColor: '#dc3545' + '33',
                    borderColor: '#dc3545' + '66',
                    pointRadius: 2,
                    pointHoverRadius: 4,
                    order: 1,
                });
            }

            new Chart(ctxPoincare, {
                type: 'scatter',
                data: { datasets },
                options: {
                    responsive: true,
                    animation: false,
                    plugins: {
                        legend: { position: 'top', labels: { font: { size: 11 } } },
                        tooltip: {
                            callbacks: {
                                label: ctx => `RRₙ=${ctx.parsed.x.toFixed(1)} ms, RRₙ₊₁=${ctx.parsed.y.toFixed(1)} ms`
                            }
                        }
                    },
                    scales: {
                        x: { title: { display: true, text: 'RRₙ (ms)' }, grid: { color: '#eef7f7' } },
                        y: { title: { display: true, text: 'RRₙ₊₁ (ms)' }, grid: { color: '#eef7f7' } }
                    }
                }
            });

        } else {
            // Fallback: SD1/SD2 bar when no series available
            new Chart(ctxPoincare, {
                type: 'bar',
                data: {
                    labels: ['SD1 (ms)', 'SD2 (ms)', 'SD2/SD1'],
                    datasets: [{
                        label: 'Derived from scalar stats',
                        data: [sd1, sd2, sd2 && sd1 ? parseFloat((sd2 / sd1).toFixed(2)) : null],
                        backgroundColor: [C.teal + 'cc', C.tealDark + 'cc', C.tealLight + 'cc'],
                        borderColor: [C.teal, C.tealDark, C.tealLight],
                        borderWidth: 2, borderRadius: 8,
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: { display: false },
                        title: { display: true, text: 'No RR series in DB (EDF required for scatter)', font: { size: 11 }, color: '#888' }
                    },
                    scales: {
                        y: { beginAtZero: true, grid: { color: '#eef7f7' } },
                        x: { grid: { display: false } }
                    }
                }
            });
        }

        // ── Complexity radar ──────────────────────────────────────────────
        const ctxComp = document.getElementById('chartComplexity').getContext('2d');
        new Chart(ctxComp, {
            type: 'radar',
            data: {
                labels: ['CSI', 'CVI', 'Sample Entropy ×100'],
                datasets: [{
                    label: 'Nonlinear Metrics',
                    data: [csi, cvi, sampen ? sampen * 100 : null],
                    backgroundColor: C.teal + '33',
                    borderColor: C.teal,
                    borderWidth: 2,
                    pointBackgroundColor: C.teal,
                }]
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: { r: { ticks: { display: false }, grid: { color: C.border } } }
            }
        });

        // ── Nonlinear interpretation ──────────────────────────────────────
        const nlIntEl = document.getElementById('nonlinearInterpretation');
        let nlTxt = nlIntEl.textContent || '';  // may already have doctor audit info
        if (sd1 !== null) {
            const ratio2 = sd2 && sd1 ? (sd2 / sd1).toFixed(2) : null;
            nlTxt += `SD1=${fmt(sd1,'ms')} reflects short-term variability (parasympathetic tone). SD2=${fmt(sd2,'ms')} reflects long-term variability.` +
                (ratio2 ? ` SD2/SD1=${ratio2} (${ratio2 > 2.5 ? 'sympathetic-dominant' : 'balanced'}).` : '');
        }
        if (sampen !== null) {
            nlTxt += ` SampEn=${sampen}: ${sampen < 1.0 ? 'reduced complexity — autonomic rigidity pattern' : 'normal complexity'}.`;
        }
        if (!sd1 && !sampen) nlTxt = (nlTxt || '') + 'No nonlinear HRV data available (requires EDF with ECG).';
        nlIntEl.textContent = nlTxt;
    }

    buildNonlinearCharts();
});