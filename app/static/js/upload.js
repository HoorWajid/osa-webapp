// app/static/js/upload.js
// Drag-and-drop zone and file input for the Upload page.
// Zero inline HTML handlers — everything wired here.

const ALLOWED = ['.edf', '.csv', '.xlsx', '.xls'];

document.addEventListener('DOMContentLoaded', function () {
    const dropZone  = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const fileLabel = document.getElementById('selectedFile');

    if (!dropZone || !fileInput) return;

    // ── Click on drop zone → open file picker ────────────────────────────
    dropZone.addEventListener('click', function () {
        fileInput.click();
    });

    // ── File chosen via picker ───────────────────────────────────────────
    fileInput.addEventListener('change', function () {
        if (fileInput.files && fileInput.files[0]) {
            _showFile(fileInput.files[0]);
        }
    });

    // ── Drag events ──────────────────────────────────────────────────────
    dropZone.addEventListener('dragover', function (e) {
        e.preventDefault();
        _setActive(true);
    });

    dropZone.addEventListener('dragleave', function () {
        _setActive(false);
    });

    dropZone.addEventListener('drop', function (e) {
        e.preventDefault();
        const files = e.dataTransfer.files;
        if (!files.length) return;

        const name  = files[0].name.toLowerCase();
        const valid = ALLOWED.some(ext => name.endsWith(ext));
        if (!valid) {
            alert('Unsupported file type. Please upload .edf, .csv, .xlsx, or .xls');
            _setActive(false);
            return;
        }

        fileInput.files = files;
        _showFile(files[0]);
    });

    // ── Helpers ──────────────────────────────────────────────────────────
    function _showFile(file) {
        const size = (file.size / (1024 * 1024)).toFixed(2);
        fileLabel.textContent = '✓ ' + file.name + '  (' + size + ' MB)';
        fileLabel.style.display = 'block';
        _setActive(true);
    }

    function _setActive(active) {
        dropZone.style.borderColor = active ? 'var(--teal)'      : 'var(--teal-light)';
        dropZone.style.background  = active ? '#e0f7f7'          : '#f0fbfb';
    }
});