// app/static/js/sidebar.js
// Sidebar toggle — collapse/expand with localStorage persistence.
// No inline HTML handlers; all wired here via addEventListener.

const STORAGE_KEY = 'osa_sidebar_collapsed';

document.addEventListener('DOMContentLoaded', function () {
    const sidebar      = document.getElementById('sidebar');
    const mainArea     = document.getElementById('mainArea');
    const overlay      = document.getElementById('sidebarOverlay');
    const toggleBtn    = document.getElementById('sidebarToggle');
    const mobileBtn    = document.getElementById('mobileMenuBtn');

    if (!sidebar) return; // public pages have no sidebar

    // ── Restore saved state (desktop only) ──────────────────────────────
    if (window.innerWidth > 768 && localStorage.getItem(STORAGE_KEY) === '1') {
        sidebar.classList.add('collapsed');
        if (mainArea) mainArea.classList.add('sidebar-collapsed');
    }

    // ── Toggle collapse (desktop) ────────────────────────────────────────
    if (toggleBtn) {
        toggleBtn.addEventListener('click', function () {
            const collapsed = sidebar.classList.toggle('collapsed');
            if (mainArea) mainArea.classList.toggle('sidebar-collapsed', collapsed);
            localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
        });
    }

    // ── Open sidebar (mobile hamburger) ─────────────────────────────────
    if (mobileBtn) {
        mobileBtn.addEventListener('click', function () {
            sidebar.classList.remove('collapsed');
            sidebar.classList.add('mobile-open');
            if (overlay) overlay.classList.add('show');
        });
    }

    // ── Close sidebar (mobile overlay tap) ──────────────────────────────
    if (overlay) {
        overlay.addEventListener('click', function () {
            sidebar.classList.remove('mobile-open');
            overlay.classList.remove('show');
        });
    }
});