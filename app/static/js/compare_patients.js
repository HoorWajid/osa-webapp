document.addEventListener('DOMContentLoaded', function() {

    // Patient 1 AHI Trend Chart
    var p1El = document.getElementById('p1ChartData');
    if (p1El && document.getElementById('p1Chart')) {
        var p1Data = JSON.parse(p1El.getAttribute('data-analyses'));
        new Chart(document.getElementById('p1Chart').getContext('2d'), {
            type: 'line',
            data: {
                labels: p1Data.map(function(a) { return a.date; }),
                datasets: [{
                    label: 'AHI Score',
                    data: p1Data.map(function(a) { return a.ahi_score; }),
                    borderColor: '#08979D',
                    backgroundColor: 'rgba(8,151,157,0.1)',
                    tension: 0.3,
                    fill: true,
                    pointRadius: 4,
                    pointHoverRadius: 7
                }]
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, title: { display: true, text: 'AHI Score' } } }
            }
        });
    }

    // Patient 2 AHI Trend Chart
    var p2El = document.getElementById('p2ChartData');
    if (p2El && document.getElementById('p2Chart')) {
        var p2Data = JSON.parse(p2El.getAttribute('data-analyses'));
        new Chart(document.getElementById('p2Chart').getContext('2d'), {
            type: 'line',
            data: {
                labels: p2Data.map(function(a) { return a.date; }),
                datasets: [{
                    label: 'AHI Score',
                    data: p2Data.map(function(a) { return a.ahi_score; }),
                    borderColor: '#8474A1',
                    backgroundColor: 'rgba(132,116,161,0.1)',
                    tension: 0.3,
                    fill: true,
                    pointRadius: 4,
                    pointHoverRadius: 7
                }]
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, title: { display: true, text: 'AHI Score' } } }
            }
        });
    }

    // Side-by-side comparison bar chart
    var compEl = document.getElementById('comparisonData');
    if (compEl && document.getElementById('comparisonChart')) {
        var p1Name   = compEl.getAttribute('data-p1-name');
        var p2Name   = compEl.getAttribute('data-p2-name');
        var p1Values = [
            parseFloat(compEl.getAttribute('data-p1-ahi')),
            parseFloat(compEl.getAttribute('data-p1-apnea')),
            parseFloat(compEl.getAttribute('data-p1-hypopnea')),
            parseFloat(compEl.getAttribute('data-p1-o2'))
        ];
        var p2Values = [
            parseFloat(compEl.getAttribute('data-p2-ahi')),
            parseFloat(compEl.getAttribute('data-p2-apnea')),
            parseFloat(compEl.getAttribute('data-p2-hypopnea')),
            parseFloat(compEl.getAttribute('data-p2-o2'))
        ];

        new Chart(document.getElementById('comparisonChart').getContext('2d'), {
            type: 'bar',
            data: {
                labels: ['AHI Score', 'Apnea Events', 'Hypopnea Events', 'O2 Desaturation'],
                datasets: [
                    {
                        label: p1Name,
                        data: p1Values,
                        backgroundColor: 'rgba(8,151,157,0.75)',
                        borderRadius: 6
                    },
                    {
                        label: p2Name,
                        data: p2Values,
                        backgroundColor: 'rgba(132,116,161,0.75)',
                        borderRadius: 6
                    }
                ]
            },
            options: {
                responsive: true,
                plugins: { legend: { position: 'top' } },
                scales: { y: { beginAtZero: true } }
            }
        });
    }
});


