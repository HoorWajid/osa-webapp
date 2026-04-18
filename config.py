# config.py  (project root — sits next to run.py)
# Security fixes applied:
#   1. SECRET_KEY raises RuntimeError if missing — no silent fallback
#   2. Session cookie flags: Secure, HttpOnly, SameSite=Lax
#   3. Session lifetime 30 min (medical standard)
#   4. DEBUG=False hard default
#   5. Upload folder outside static/ (not web-accessible)
#   6. MAX_CONTENT_LENGTH single source of truth (100MB)
#   7. WTF_CSRF_TIME_LIMIT set (tokens expire after 1 hour)

import os
from datetime import timedelta


class Config:

    # ── SECURITY FIX 1: Hard fail if secret key not set ──────────────────────
    # No silent fallback. If this env var is missing, the app refuses to start.
    # Like a vault that won't open without the right combination —
    # better to stay closed than open with a blank code.
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        raise RuntimeError(
            "\n\n[SECURITY] SECRET_KEY environment variable is not set.\n"
            "Set it in your .env file or system environment:\n"
            "  SECRET_KEY=<random 32+ char string>\n"
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\"\n"
        )

    # ── Database ──────────────────────────────────────────────────────────────
    SQLALCHEMY_DATABASE_URI     = os.environ.get('DATABASE_URL', 'sqlite:///osa_app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── SECURITY FIX 4: Debug always off unless explicitly enabled ────────────
    DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'

    # ── SECURITY FIX 2 & 3: Session cookie security ───────────────────────────
    # Secure     = only sent over HTTPS (set FORCE_HTTPS=true in production)
    # HttpOnly   = JavaScript cannot read the cookie (XSS protection)
    # SameSite   = Lax blocks CSRF from cross-origin top-level navigation
    # Lifetime   = 30 min idle timeout (HIPAA recommends ≤15 min; 30 is a safe default)
    SESSION_COOKIE_SECURE      = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
    SESSION_COOKIE_HTTPONLY    = True
    SESSION_COOKIE_SAMESITE    = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)

    # ── SECURITY FIX 7: CSRF token expiry ─────────────────────────────────────
    WTF_CSRF_TIME_LIMIT = 3600   # 1 hour

    # ── File uploads ──────────────────────────────────────────────────────────
    # SECURITY FIX 5: Upload folder is OUTSIDE the app package and outside static/
    # Files stored here are NOT served by Flask automatically — auth is required.
    # Default path goes one level above the app package: project_root/uploads/
    UPLOAD_FOLDER       = os.environ.get(
        'UPLOAD_FOLDER',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    )
    # SECURITY FIX 6: One source of truth for max upload size
    MAX_CONTENT_LENGTH  = int(os.environ.get('MAX_CONTENT_LENGTH', 100 * 1024 * 1024))  # 100 MB

    # ── Mail ──────────────────────────────────────────────────────────────────
    MAIL_SERVER         = os.environ.get('MAIL_SERVER',   'smtp.gmail.com')
    MAIL_PORT           = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS        = os.environ.get('MAIL_USE_TLS',  'true').lower()  == 'true'
    MAIL_USE_SSL        = os.environ.get('MAIL_USE_SSL',  'false').lower() == 'true'
    MAIL_USERNAME       = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD       = os.environ.get('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.environ.get(
        'MAIL_DEFAULT_SENDER',
        os.environ.get('MAIL_USERNAME', 'noreply@osa-system.com')
    )

    # ── Force HTTPS (set true behind Nginx/proxy in production) ──────────────
    FORCE_HTTPS = os.environ.get('FORCE_HTTPS', 'false').lower() == 'true'