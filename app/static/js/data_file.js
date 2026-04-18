// app/static/js/data_files.js
// Delete confirmation modal for My Data Files page.
// Reads file-id and file-name from data-* attributes on each delete button.

document.addEventListener('DOMContentLoaded', function () {
    const deleteModal    = document.getElementById('deleteModal');
    const deleteFileName = document.getElementById('deleteFileName');
    const deleteForm     = document.getElementById('deleteForm');

    if (!deleteModal) return;

    const bsModal = new bootstrap.Modal(deleteModal);

    // Wire every delete button
    document.querySelectorAll('.btn-delete').forEach(function (btn) {
        btn.addEventListener('click', function () {
            const fileId   = btn.dataset.fileId;
            const fileName = btn.dataset.fileName;
            deleteFileName.textContent = fileName;
            deleteForm.action = '/patient/data-file/' + fileId + '/delete';
            bsModal.show();
        });
    });
});