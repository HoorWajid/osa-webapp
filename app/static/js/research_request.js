// app/static/js/research_request.js
// Controls the research request form — shows/hides the specific-patient
// checkbox panel depending on the selected data scope.

document.addEventListener('DOMContentLoaded', function () {
    const scopeSelect = document.getElementById('scopeSelect');
    if (scopeSelect) {
        // Initialise on page load (handles browser back-button state)
        updateScopeUI(scopeSelect.value);
        scopeSelect.addEventListener('change', function () {
            updateScopeUI(this.value);
        });
    }
});

function updateScopeUI(val) {
    const panel = document.getElementById('specificPatientsPanel');
    if (panel) {
        panel.style.display = val === 'specific' ? '' : 'none';
    }
}