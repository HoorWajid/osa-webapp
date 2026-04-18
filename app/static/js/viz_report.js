// app/static/js/viz_report.js
// Full OSA Report — Chart.js charts, metric tables, PDF download via jsPDF + html2canvas

document.addEventListener('DOMContentLoaded', function () {
    const features = JSON.parse(document.getElementById('featuresData').textContent);
    const analysis = JSON.parse(document.getElementById('analysisData').textContent);

    const C = {
        teal:      '#08979D',
        tealDark:  '#05585C',
        tealLight: '#6EC6CA',
        purple:    '#8474A1',
        lavender:  '#CCA8D8',
        green:     '#198754',
        orange:    '#fd7e14',
        red:       '#dc3545',
        border:    '#d0ecee',
    };

    // ── helpers ──────────────────────────────────────────────────────────────
    function v(key, dec = 2) {
        const val = features[key];
        if (val === undefined || val === null || isNaN(val)) return null;
        return parseFloat(parseFloat(val).toFixed(dec));
    }
    function fmt(val, unit = '', fallback = 'N/A') {
        return val === null ? fallback : val + (unit ? ' ' + unit : '');
    }
    function pct(val) { return fmt(val, '%'); }

    // ── Status badge in header ────────────────────────────────────────────────
    const statusEl = document.getElementById('reportStatusBadge');
    if (statusEl) {
        statusEl.textContent = analysis.osa_detected ? '⚠ OSA Detected' : '✓ No OSA';
        statusEl.style.color = analysis.osa_detected ? '#ffd700' : '#a8edea';
    }

    // ── Footer date ───────────────────────────────────────────────────────────
    const footerDate = document.getElementById('reportFooterDate');
    if (footerDate) {
        footerDate.textContent = 'Generated: ' + new Date().toLocaleString();
    }

    // ── AHI pointer position ─────────────────────────────────────────────────
    const ahi = analysis.ahi_score || 0;
    const ahiPointer = document.getElementById('ahiPointer');
    if (ahiPointer) {
        // Scale: 0 maps to 0%, 40 maps to 100%
        const pctPos = Math.min(Math.max((ahi / 40) * 100, 2), 98);
        ahiPointer.style.left = pctPos + '%';
        ahiPointer.textContent = ahi.toFixed(1);
    }

    // ── Primary metrics grid ─────────────────────────────────────────────────
    function metricBox(value, label, unit, cssClass) {
        return `<div class="metric-box ${cssClass || ''}">
            <div class="m-value">${value}</div>
            <div class="m-unit">${unit}</div>
            <div class="m-label">${label}</div>
        </div>`;
    }
    function severityClass(sev) {
        return { normal: '', mild: '', moderate: 'warn', severe: 'danger' }[sev] || '';
    }

    const pm = document.getElementById('primaryMetrics');
    if (pm) {
        pm.innerHTML =
            metricBox(ahi.toFixed(1), 'AHI Score', 'events/hr', severityClass(analysis.severity)) +
            metricBox(analysis.severity ? analysis.severity.charAt(0).toUpperCase() + analysis.severity.slice(1) : '—', 'Severity', '', severityClass(analysis.severity)) +
            metricBox(((analysis.confidence || 0) * 100).toFixed(1), 'Confidence', '%', 'highlight') +
            metricBox(analysis.apnea_events || 0, 'Apnea Events', 'count', '') +
            metricBox(analysis.hypopnea_events || 0, 'Hypopnea Events', 'count', '') +
            metricBox(parseFloat(analysis.oxygen_desaturation || 0).toFixed(1), 'ODI', '%', ahi > 15 ? 'warn' : '');
    }

    // ── SpO₂ gradient bar markers ─────────────────────────────────────────────
    const spo2Mean = v('spo2_mean', 1);
    const spo2Min  = v('spo2_min', 1);
    // Range: 70–100% → 0–100%
    function spo2ToPct(val) { return val === null ? null : Math.min(Math.max(((val - 70) / 30) * 100, 0), 100); }

    const minMarker  = document.getElementById('spo2MinMarker');
    const meanMarker = document.getElementById('spo2MeanMarker');
    const markerLbl  = document.getElementById('spo2MarkerLabel');
    if (minMarker  && spo2Min  !== null) minMarker.style.left  = spo2ToPct(spo2Min) + '%';
    if (meanMarker && spo2Mean !== null) meanMarker.style.left = spo2ToPct(spo2Mean) + '%';
    if (markerLbl) {
        markerLbl.innerHTML =
            `<span style="color:var(--teal-dark);">▲ Min: ${fmt(spo2Min,'%')}</span>` +
            `&nbsp;&nbsp;<span style="color:var(--teal);">▮ Mean: ${fmt(spo2Mean,'%')}</span>`;
    }

    // ── HRV table ─────────────────────────────────────────────────────────────
    const hrvRows = [
        ['SDNN',          v('sdnn'),       'ms',   '50–100 ms'],
        ['RMSSD',         v('rmssd'),      'ms',   '30–80 ms'],
        ['pNN50',         v('pnn50'),      '%',    '≥18%'],
        ['pNN20',         v('pnn20'),      '%',    '≥35%'],
        ['Mean NN',       v('mean_nni') || v('mean_rr'), 'ms', '700–900 ms'],
        ['LF Power',      v('lf'),         'ms²',  '546–2950 ms²'],
        ['HF Power',      v('hf'),         'ms²',  '218–1810 ms²'],
        ['LF/HF Ratio',   v('lf_hf_ratio'),'',     '0.5–2.5'],
        ['LF nu',         v('lfnu'),       'nu',   '20–80 nu'],
        ['HF nu',         v('hfnu'),       'nu',   '20–80 nu'],
        ['Total Power',   v('total_power'),'ms²',  '>1000 ms²'],
        ['SD1',           v('sd1'),        'ms',   '—'],
        ['SD2',           v('sd2'),        'ms',   '—'],
        ['CSI',           v('csi'),        '',     '—'],
        ['CVI',           v('cvi'),        '',     '—'],
        ['Sample Entropy',v('sample_entropy') || v('sampen'), '', '1.0–2.5'],
        ['R-peaks',       v('total_peaks', 0), 'count', '—'],
    ].filter(r => r[1] !== null);

    const hrvBody = document.getElementById('hrvTableBody');
    if (hrvBody) {
        hrvBody.innerHTML = hrvRows.map(([label, val, unit, ref]) =>
            `<tr><td>${label}</td><td>${fmt(val, unit)}</td><td style="text-align:right;color:var(--text-muted);font-weight:400;">${ref}</td></tr>`
        ).join('') || '<tr><td colspan="3" style="text-align:center;color:var(--text-muted);">No HRV data available (requires EDF with ECG signal)</td></tr>';
    }

    // HRV callout
    const sdnn = v('sdnn');
    const rmssd = v('rmssd');
    const lf_hf = v('lf_hf_ratio');
    let hrvMsg = 'No HRV data available. For full HRV analysis, please upload an EDF file with ECG signal.';
    if (sdnn !== null) {
        const sdnnStatus = sdnn < 20 ? 'severely reduced' : sdnn < 50 ? 'below normal' : 'within normal range';
        hrvMsg = `SDNN of ${fmt(sdnn,'ms')} is ${sdnnStatus}. RMSSD of ${fmt(rmssd,'ms')} reflects ${rmssd !== null && rmssd >= 30 ? 'adequate' : 'reduced'} parasympathetic tone.`;
        if (lf_hf !== null) hrvMsg += ` LF/HF ratio of ${lf_hf} indicates ${lf_hf > 2.5 ? 'elevated sympathetic dominance, commonly seen in OSA' : 'balanced sympathovagal tone'}.`;
    }
    const hrvCallout = document.getElementById('hrvCallout');
    if (hrvCallout) {
        hrvCallout.textContent = hrvMsg;
        if (sdnn !== null && sdnn < 50) hrvCallout.classList.add('warn');
    }

    // ── SpO₂ table ────────────────────────────────────────────────────────────
    const spo2Rows = [
        ['Mean SpO₂',      v('spo2_mean',1),   '%', '≥95%'],
        ['Median SpO₂',    v('spo2_median',1), '%', '≥95%'],
        ['Min SpO₂',       v('spo2_min',1),    '%', '≥88%'],
        ['Max SpO₂',       v('spo2_max',1),    '%', '≤100%'],
        ['Std Deviation',  v('spo2_std',2),    '%', '<2%'],
        ['CV',             v('spo2_cv',2),     '%', '<3%'],
        ['P5 (5th pctile)',v('spo2_p5',1),     '%', '≥90%'],
        ['P10',            v('spo2_p10',1),    '%', '≥91%'],
        ['P25',            v('spo2_p25',1),    '%', '≥93%'],
        ['P90',            v('spo2_p90',1),    '%', '≥96%'],
        ['P95',            v('spo2_p95',1),    '%', '≥97%'],
        ['Time <85%',      v('time_below_85',1),'%','<1%'],
        ['Time <88%',      v('time_below_88',1),'%','<1%'],
        ['Time <90%',      v('time_below_90',1),'%','<5%'],
        ['Time <93%',      v('time_below_93',1),'%','<10%'],
        ['Time <95%',      v('time_below_95',1),'%','<20%'],
        ['Time ≥95%',      v('time_above_95',1),'%','>80%'],
        ['ODI',            parseFloat((analysis.oxygen_desaturation||0).toFixed(1)),'%','<5%'],
        ['Recording Hours',v('recording_hours',2),'h','—'],
    ].filter(r => r[1] !== null);

    const spo2Body = document.getElementById('spo2TableBody');
    if (spo2Body) {
        spo2Body.innerHTML = spo2Rows.map(([label, val, unit, ref]) =>
            `<tr><td>${label}</td><td>${fmt(val, unit)}</td><td style="text-align:right;color:var(--text-muted);font-weight:400;">${ref}</td></tr>`
        ).join('') || '<tr><td colspan="3" style="text-align:center;color:var(--text-muted);">No SpO₂ data available</td></tr>';
    }

    // SpO₂ callout
    const mean1 = v('spo2_mean',1);
    const tb90  = v('time_below_90',1);
    let spo2Msg = 'No SpO₂ data available.';
    if (mean1 !== null) {
        spo2Msg = `Mean SpO₂ of ${mean1}% is ${mean1 >= 95 ? 'within normal range' : mean1 >= 90 ? 'mildly reduced' : 'significantly reduced'}.`;
        if (tb90 !== null) spo2Msg += ` ${tb90.toFixed(1)}% of the recording was spent below 90% SpO₂${tb90 > 5 ? ' — clinically significant desaturation burden' : ''}.`;
    }
    const spo2Callout = document.getElementById('spo2Callout');
    if (spo2Callout) {
        spo2Callout.textContent = spo2Msg;
        if (mean1 !== null && mean1 < 90) spo2Callout.classList.add('danger');
        else if (mean1 !== null && mean1 < 95) spo2Callout.classList.add('warn');
    }

    // ── Nonlinear table ───────────────────────────────────────────────────────
    const nlRows = [
        ['SD1 (short-term)', v('sd1'), 'ms', 'Parasympathetic tone'],
        ['SD2 (long-term)',  v('sd2'), 'ms', 'Sympathetic + parasympathetic'],
        ['SD2/SD1 ratio',    v('sd1') && v('sd2') ? parseFloat((v('sd2')/v('sd1')).toFixed(2)) : null, '', 'Autonomic balance'],
        ['CSI', v('csi'), '', 'Cardiac Sympathetic Index'],
        ['CVI', v('cvi'), '', 'Cardiac Vagal Index'],
        ['Sample Entropy', v('sample_entropy') || v('sampen'), '', 'Signal complexity'],
    ].filter(r => r[1] !== null);

    const nlBody = document.getElementById('nonlinearTableBody');
    if (nlBody) {
        nlBody.innerHTML = nlRows.map(([label, val, unit, interp]) =>
            `<tr><td>${label}</td><td>${fmt(val, unit)}</td><td style="text-align:right;color:var(--text-muted);font-weight:400;">${interp}</td></tr>`
        ).join('') || '<tr><td colspan="3" style="text-align:center;color:var(--text-muted);">No nonlinear HRV data available</td></tr>';
    }

    // ── Summary callout ───────────────────────────────────────────────────────
    const summaryCallout = document.getElementById('summaryCallout');
    if (summaryCallout) {
        const sev = analysis.severity || 'unknown';
        let summary = `This sleep study analysis of "${analysis.file_name}" was completed on ${analysis.completed_at}. `;
        if (analysis.osa_detected) {
            summary += `OSA was detected with ${sev} severity (AHI = ${ahi.toFixed(1)} events/hour). `;
            summary += `The analysis found ${analysis.apnea_events} apnea and ${analysis.hypopnea_events} hypopnea events. `;
            if (mean1 !== null) summary += `Mean SpO₂ was ${mean1}% with ODI of ${(analysis.oxygen_desaturation||0).toFixed(1)}%. `;
            if (sdnn !== null) summary += `HRV analysis showed SDNN of ${fmt(sdnn,'ms')} and RMSSD of ${fmt(rmssd,'ms')}. `;
            summary += `Clinical review and specialist consultation is recommended.`;
        } else {
            summary += `No significant OSA was detected (AHI = ${ahi.toFixed(1)} events/hour, which is within the normal range of <5). `;
            if (mean1 !== null) summary += `SpO₂ was well maintained with a mean of ${mean1}%. `;
            summary += `Routine follow-up is advised.`;
        }
        summaryCallout.textContent = summary;
    }

    // ── CHARTS ───────────────────────────────────────────────────────────────
    // HRV summary radar/bar
    const ctxHRV = document.getElementById('chartHRVSummary').getContext('2d');
    new Chart(ctxHRV, {
        type: 'bar',
        data: {
            labels: ['SDNN', 'RMSSD', 'pNN50 (%)', 'LF/HF', 'SD1', 'SD2'],
            datasets: [{
                label: 'Measured',
                data: [v('sdnn'), v('rmssd'), v('pnn50'), v('lf_hf_ratio'), v('sd1'), v('sd2')],
                backgroundColor: [C.teal+'cc',C.tealLight+'cc',C.purple+'cc',C.lavender+'cc',C.tealDark+'cc',C.teal+'88'],
                borderColor: [C.teal,C.tealLight,C.purple,C.lavender,C.tealDark,C.teal],
                borderWidth: 2,
                borderRadius: 6,
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, grid: { color: '#eef7f7' } },
                x: { grid: { display: false }, ticks: { font: { size: 10 } } }
            }
        }
    });

    // SpO₂ bar chart
    const threshKeys = ['time_below_85','time_below_88','time_below_90','time_below_93','time_below_95','time_above_95'];
    const threshLabels = ['<85%','<88%','<90%','<93%','<95%','≥95%'];
    const threshColors = [C.red,'#e67e22',C.orange,'#f0c040',C.tealLight,C.green];
    const ctxSpo2 = document.getElementById('chartSpo2Summary').getContext('2d');
    new Chart(ctxSpo2, {
        type: 'bar',
        data: {
            labels: threshLabels,
            datasets: [{
                label: '% of Recording',
                data: threshKeys.map(k => v(k,1)),
                backgroundColor: threshColors.map(c => c+'cc'),
                borderColor: threshColors,
                borderWidth: 2,
                borderRadius: 6,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                x: { beginAtZero: true, max: 100, grid: { color: '#eef7f7' }, ticks: { callback: v => v+'%' } },
                y: { grid: { display: false } }
            }
        }
    });

    // Nonlinear bar
    const ctxNL = document.getElementById('chartNonlinearSummary').getContext('2d');
    new Chart(ctxNL, {
        type: 'bar',
        data: {
            labels: ['SD1 (ms)', 'SD2 (ms)', 'CSI', 'CVI'],
            datasets: [{
                label: 'Value',
                data: [v('sd1'), v('sd2'), v('csi'), v('cvi')],
                backgroundColor: [C.teal+'cc', C.tealDark+'cc', C.purple+'cc', C.lavender+'cc'],
                borderColor: [C.teal, C.tealDark, C.purple, C.lavender],
                borderWidth: 2,
                borderRadius: 6,
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

    // ════════════════════════════════════════════════════════════════════════
    // PDF DOWNLOAD
    // ════════════════════════════════════════════════════════════════════════
    const btnDownload = document.getElementById('btnDownloadPDF');
    if (btnDownload) {
        btnDownload.addEventListener('click', async function () {
            btnDownload.disabled = true;
            btnDownload.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Generating PDF…';

            try {
                const { jsPDF } = window.jspdf;
                const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });
                const reportEl = document.getElementById('reportDocument');

                // Temporarily hide action bar for cleaner capture
                const actionBar = document.querySelector('.report-actions-bar');
                if (actionBar) actionBar.style.display = 'none';

                const canvas = await html2canvas(reportEl, {
                    scale: 2,
                    useCORS: true,
                    logging: false,
                    backgroundColor: '#ffffff',
                });

                if (actionBar) actionBar.style.display = '';

                const imgData = canvas.toDataURL('image/jpeg', 0.92);
                const pageW   = doc.internal.pageSize.getWidth();
                const pageH   = doc.internal.pageSize.getHeight();
                const margin  = 10;
                const usableW = pageW - margin * 2;
                const ratio   = canvas.width / canvas.height;
                const imgH    = usableW / ratio;
                const pages   = Math.ceil(imgH / (pageH - margin * 2));

                for (let i = 0; i < pages; i++) {
                    if (i > 0) doc.addPage();
                    const srcY  = i * (canvas.height / pages);
                    const srcH  = canvas.height / pages;
                    const sliceCanvas = document.createElement('canvas');
                    sliceCanvas.width  = canvas.width;
                    sliceCanvas.height = srcH;
                    sliceCanvas.getContext('2d').drawImage(canvas, 0, -srcY);
                    const sliceData = sliceCanvas.toDataURL('image/jpeg', 0.92);
                    doc.addImage(sliceData, 'JPEG', margin, margin, usableW, pageH - margin * 2);
                }

                const fileName = `OSA_Report_${analysis.file_name.replace(/\.[^.]+$/, '')}_${analysis.completed_at.replace(/[: ]/g,'-')}.pdf`;
                doc.save(fileName);
            } catch (err) {
                console.error('PDF generation error:', err);
                alert('PDF generation failed. Please try Print → Save as PDF instead.');
            } finally {
                btnDownload.disabled = false;
                btnDownload.innerHTML = '<i class="bi bi-download me-1"></i>Download PDF';
            }
        });
    }

    // ── Print button ──────────────────────────────────────────────────────────
    const btnPrint = document.getElementById('btnPrint');
    if (btnPrint) {
        btnPrint.addEventListener('click', function () { window.print(); });
    }
});