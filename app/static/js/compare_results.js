document.addEventListener('DOMContentLoaded', function() {
    var chartDataEl = document.getElementById('chart-data');
    if (!chartDataEl) return;

    var analysesData = JSON.parse(chartDataEl.getAttribute('data-analyses'));
    if (!analysesData || analysesData.length === 0) return;

    // AHI Trend Chart
    new Chart(document.getElementById('ahiChart').getContext('2d'), {
        type: 'line',
        data: {
            labels: analysesData.map(function(a) { return a.date; }),
            datasets: [{
                label: 'AHI Score',
                data: analysesData.map(function(a) { return a.ahi_score; }),
                borderColor: 'rgb(13, 110, 253)',
                backgroundColor: 'rgba(13, 110, 253, 0.1)',
                tension: 0.3,
                fill: true,
                pointRadius: 5,
                pointHoverRadius: 8
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: true },
                tooltip: { mode: 'index' }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    title: { display: true, text: 'AHI Score' }
                }
            }
        }
    });

    // Severity Distribution Chart
    var severityCounts = { normal: 0, mild: 0, moderate: 0, severe: 0 };
    analysesData.forEach(function(a) {
        if (severityCounts[a.severity] !== undefined) {
            severityCounts[a.severity]++;
        }
    });

    new Chart(document.getElementById('severityChart').getContext('2d'), {
        type: 'doughnut',
        data: {
            labels: ['Normal', 'Mild', 'Moderate', 'Severe'],
            datasets: [{
                data: [
                    severityCounts.normal,
                    severityCounts.mild,
                    severityCounts.moderate,
                    severityCounts.severe
                ],
                backgroundColor: ['#198754', '#ffc107', '#fd7e14', '#dc3545']
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { position: 'bottom' } }
        }
    });

    // Metrics Bar Chart (last 5)
    var recent = analysesData.slice(0, 5).reverse();
    new Chart(document.getElementById('metricsChart').getContext('2d'), {
        type: 'bar',
        data: {
            labels: recent.map(function(a) { return a.date; }),
            datasets: [
                {
                    label: 'AHI Score',
                    data: recent.map(function(a) { return a.ahi_score; }),
                    backgroundColor: 'rgba(13,110,253,0.7)'
                },
                {
                    label: 'Apnea Events',
                    data: recent.map(function(a) { return a.apnea_events; }),
                    backgroundColor: 'rgba(220,53,69,0.7)'
                },
                {
                    label: 'Hypopnea Events',
                    data: recent.map(function(a) { return a.hypopnea_events; }),
                    backgroundColor: 'rgba(255,193,7,0.7)'
                },
                {
                    label: 'O2 Desaturation',
                    data: recent.map(function(a) { return a.oxygen_desaturation; }),
                    backgroundColor: 'rgba(25,135,84,0.7)'
                }
            ]
        },
        options: {
            responsive: true,
            plugins: { legend: { position: 'top' } },
            scales: { y: { beginAtZero: true } }
        }
    });
});