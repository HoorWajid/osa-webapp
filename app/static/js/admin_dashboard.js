document.addEventListener('DOMContentLoaded', function() {

    // Timestamp
    var refreshEl = document.getElementById('lastRefresh');
    if (refreshEl) {
        refreshEl.textContent = new Date().toLocaleTimeString();
    }

    // User search
    var userSearch = document.getElementById('userSearch');
    if (userSearch) {
        userSearch.addEventListener('input', function() {
            var q = this.value.toLowerCase();
            document.querySelectorAll('#usersTable tbody tr').forEach(function(row) {
                row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
            });
        });
    }

    // Log filter
    var logFilter = document.getElementById('logFilter');
    if (logFilter) {
        logFilter.addEventListener('change', function() {
            var val = this.value;
            document.querySelectorAll('#auditTable tbody tr').forEach(function(row) {
                if (val === 'all') {
                    row.style.display = '';
                } else {
                    row.style.display = (row.getAttribute('data-event') === val) ? '' : 'none';
                }
            });
        });
    }

    // Read chart data from hidden element
    var statsEl = document.getElementById('stats-data');
    if (!statsEl) return;

    var rolesData    = JSON.parse(statsEl.getAttribute('data-roles'));
    var severityData = JSON.parse(statsEl.getAttribute('data-severity'));
    var osaData      = JSON.parse(statsEl.getAttribute('data-osa'));

    // Roles Chart
    new Chart(document.getElementById('rolesChart').getContext('2d'), {
        type: 'doughnut',
        data: {
            labels: ['Admins', 'Doctors', 'Patients'],
            datasets: [{
                data: rolesData,
                backgroundColor: ['#dc3545', '#0d6efd', '#198754']
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { position: 'bottom' } }
        }
    });

    // Severity Chart
    new Chart(document.getElementById('severityChart').getContext('2d'), {
        type: 'bar',
        data: {
            labels: ['Normal', 'Mild', 'Moderate', 'Severe'],
            datasets: [{
                label: 'Count',
                data: severityData,
                backgroundColor: ['#198754', '#ffc107', '#fd7e14', '#dc3545']
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true } }
        }
    });

    // OSA Chart
    new Chart(document.getElementById('osaChart').getContext('2d'), {
        type: 'pie',
        data: {
            labels: ['OSA Detected', 'Normal'],
            datasets: [{
                data: osaData,
                backgroundColor: ['#ffc107', '#198754']
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { position: 'bottom' } }
        }
    });
});