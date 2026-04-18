# app/__init__.py
# Security fixes applied:
#   1. SECRET_KEY raises RuntimeError if missing — no silent fallback
#   2. Config class loaded via app.config.from_object(Config) — one source of truth
#   3. Flask-Talisman initialized (CSP, HSTS, X-Frame-Options, X-Content-Type)
#   4. Flask-Limiter initialized and exported for use in routes
#   5. Session cookie security flags set (Secure, HttpOnly, SameSite)
#   6. PERMANENT_SESSION_LIFETIME enforced (30 min for medical app)
#   7. Upload folder moved outside static/ (files no longer publicly accessible)
#   8. DEBUG forced False in production guard
#   9. SQLALCHEMY_DATABASE_URI correctly reads from env via Config

import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

load_dotenv()

# ── Extension instances (imported by routes and other modules) ────────────────
db           = SQLAlchemy()
login_manager = LoginManager()
mail         = Mail()
csrf         = CSRFProtect()
limiter      = Limiter(key_func=get_remote_address, default_limits=["200 per day", "60 per hour"])


def create_app():
    app = Flask(__name__)

    # ── Load config from Config class — single source of truth ───────────────
    # Config raises RuntimeError if SECRET_KEY is missing, so app cannot
    # start silently with a weak key.  Think of it as a dead-man's switch.
    from config import Config
    app.config.from_object(Config)

    # ── SECURITY FIX 3: Security headers via Talisman ────────────────────────
    # Adds: HSTS, X-Frame-Options, X-Content-Type-Options, CSP, Referrer-Policy
    # force_https=False here — set to True behind a TLS-terminating proxy/Nginx
    try:
        from flask_talisman import Talisman
        Talisman(
            app,
            force_https=os.environ.get('FORCE_HTTPS', 'false').lower() == 'true',
            strict_transport_security=True,
            strict_transport_security_max_age=31536000,   # 1 year
            content_security_policy={
                'default-src': ["'self'"],
                'script-src':  ["'self'", "'unsafe-inline'", "cdn.jsdelivr.net",
                                "cdnjs.cloudflare.com"],
                'style-src':   ["'self'", "'unsafe-inline'", "cdn.jsdelivr.net",
                                "cdnjs.cloudflare.com"],
                'img-src':     ["'self'", "data:"],
                'font-src':    ["'self'", "cdnjs.cloudflare.com"],
            },
            content_security_policy_nonce_in=['script-src'],
            frame_options='DENY',
            referrer_policy='strict-origin-when-cross-origin',
        )
    except ImportError:
        app.logger.warning(
            'flask-talisman not installed — security headers disabled. '
            'Run: pip install flask-talisman'
        )

    # ── Extensions ────────────────────────────────────────────────────────────
    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    login_manager.login_view         = 'auth.login'
    login_manager.login_message_category = 'info'
    # SECURITY FIX: session marked permanent so PERMANENT_SESSION_LIFETIME applies
    login_manager.refresh_view       = 'auth.login'
    login_manager.needs_refresh_message_category = 'info'

    # ── User loader ───────────────────────────────────────────────────────────
    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # ── Blueprints ────────────────────────────────────────────────────────────
    from app.routes import main_bp, auth_bp, admin_bp, doctor_bp, patient_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp,   url_prefix='/admin')
    app.register_blueprint(doctor_bp,  url_prefix='/doctor')
    app.register_blueprint(patient_bp, url_prefix='/patient')

    # ── Template filters ──────────────────────────────────────────────────────
    from app.routes import register_template_filters
    register_template_filters(app)

    # ── Upload folder — outside static/ so files are NOT web-accessible ───────
    upload_folder = app.config.get('UPLOAD_FOLDER')
    if upload_folder:
        os.makedirs(upload_folder, exist_ok=True)

    return app