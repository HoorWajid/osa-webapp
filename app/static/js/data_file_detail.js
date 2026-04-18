// app/static/js/data_file_detail.js
// Security fixes:
//   - fileId validated as numeric before use in form action (defence in depth)
//   - textContent already used correctly (confirmed in audit)

document.addEventListener('DOMContentLoaded', function () {
    const deleteModal    = document.getElementById('deleteModal');
    const deleteFileName = document.getElementById('deleteFileName');
    const deleteForm     = document.getElementById('deleteForm');
    const refreshBtn     = document.getElementById('btnRefresh');

    // Refresh button (shown when analysis is processing)
    if (refreshBtn) {
        refreshBtn.addEventListener('click', function () {
            location.reload();
        });
    }

    if (!deleteModal) return;

    const bsModal = new bootstrap.Modal(deleteModal);

    // Delete button(s)
    document.querySelectorAll('.btn-delete').forEach(function (btn) {
        btn.addEventListener('click', function () {
            const fileId   = btn.dataset.fileId;
            const fileName = btn.dataset.fileName;

            // SECURITY FIX: validate fileId is a positive integer before
            // embedding in the form action URL — defence in depth even though
            // fileId comes from {{ data_file.id }} (a DB integer)
            if (!fileId || !/^\d+$/.test(fileId)) {
                console.error('Invalid file ID:', fileId);
                return;
            }

            // SECURITY: textContent prevents XSS from crafted filenames
            deleteFileName.textContent = fileName;
            deleteForm.action = '/patient/data-file/' + fileId + '/delete';
            bsModal.show();
        });
    });
});