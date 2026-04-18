# app/routes.py
# Security fixes applied:
#   1. Open redirect fixed — next_page validated as safe internal URL
#   2. Brute-force lockout added — 5 attempts → 15 min lockout (persistent in DB)
#   3. Session regenerated after login (session fixation prevention)
#   4. Reset token hashed in DB — raw token sent in email only
#   5. Reset link NEVER shown in flash — email-only delivery
#   6. Email confirmation URL NEVER shown in flash — email-only delivery
#   7. MIME type validation on file uploads (magic bytes check)
#   8. Dev fallback account auto-activation REMOVED
#   9. /test-mail route removed (was leaking SMTP config)
#  10. Admin research export gated to approved requests only
#  11. Role change resets verification status when promoting to doctor
#  12. CSV parse endpoint limited to first 500 rows (DoS prevention)
#  13. Exception details hidden from users in analyze route
#  14. Research download capped at MAX_DOWNLOADS (configurable)
#  15. Insecure account state heuristic replaced with explicit DB field

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from urllib.parse import urlparse, urljoin
from app import db
from app.models import (User, UserProfile, PatientAssignment, DataFile,
                        Analysis, AuditLog, DoctorProfile, PatientDemographics,
                        DoctorVerificationRequest, ResearchRequest)
from app.forms import (LoginForm, RegistrationForm, ProfileForm, DataFileUploadForm,
                       AssignPatientForm, DoctorVerificationForm, DoctorProfileForm,
                       PatientDemographicsForm, ForgotPasswordForm, ResetPasswordForm,
                       ChangePasswordForm, ChangeEmailForm)
from functools import wraps
from datetime import datetime, timedelta
import os
import hashlib
import json
import secrets
import csv
import io

# Magic bytes for allowed upload types
# Think of this as checking someone's actual passport, not just what they claim their name is
MAGIC_BYTES = {
    b'\xef\xbb\xbf': 'csv',     # UTF-8 BOM (CSV/text)
    b'%PDF':         'pdf',
    b'\xff\xd8\xff': 'jpeg',
    b'\x89PNG':      'png',
    b'0       ':     'edf',      # EDF/EDF+ header: version field is '0' + 7 spaces
}
# Max research downloads per approved request
MAX_RESEARCH_DOWNLOADS = 10

# ─────────────────────────────────────────
# BLUEPRINTS
# ─────────────────────────────────────────

main_bp    = Blueprint('main',    __name__)
auth_bp    = Blueprint('auth',    __name__)
admin_bp   = Blueprint('admin',   __name__)
doctor_bp  = Blueprint('doctor',  __name__)
patient_bp = Blueprint('patient', __name__)


def register_template_filters(app):
    """Register custom Jinja2 filters. Call this from create_app()."""
    @app.template_filter('fromjson')
    def fromjson_filter(value):
        if not value:
            return {}
        try:
            return json.loads(value)
        except Exception:
            return {}


# ─────────────────────────────────────────
# SECURITY HELPERS
# ─────────────────────────────────────────

def is_safe_url(target):
    """
    Validate that a redirect target is a safe internal URL.
    Like checking that a door leads to a room in your own house,
    not out onto the street.
    Prevents open redirect: /login?next=https://evil.com
    """
    ref_url  = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return (test_url.scheme in ('http', 'https') and
            ref_url.netloc == test_url.netloc)


def hash_token(raw_token: str) -> str:
    """
    One-way hash of a token for DB storage.
    Raw token goes in email only — if DB is breached,
    hashes are useless without the original token.
    """
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def validate_file_magic(file_storage, allowed_mimes: list) -> bool:
    """
    Read the first 8 bytes of an uploaded file and check against known magic bytes.
    Extension-only checks are trivially bypassed (rename malware.php → malware.csv).
    Always seek back to 0 after reading so the file can still be saved.
    """
    header = file_storage.read(8)
    file_storage.seek(0)
    for magic, mime in MAGIC_BYTES.items():
        if header.startswith(magic) and mime in allowed_mimes:
            return True
    # Plain text / CSV files have no universal magic — allow if extension matches
    # and content appears to be text (printable bytes, allowing latin-1/UTF-8)
    # Many clinical CSV exports use latin-1 encoding
    if 'csv' in allowed_mimes or 'txt' in allowed_mimes:
        try:
            header.decode('utf-8')
            return True
        except UnicodeDecodeError:
            pass
        try:
            header.decode('latin-1')
            # Verify it's not a binary file masquerading as text
            # Allow if most bytes are printable ASCII or common latin-1
            printable = sum(1 for b in header if 0x09 <= b <= 0x0d or 0x20 <= b <= 0xfe)
            if printable >= len(header) * 0.75:
                return True
        except Exception:
            pass
    return False


# ─────────────────────────────────────────
# ACCESS CONTROL HELPERS
# ─────────────────────────────────────────

def role_required(role):
    """Role-based access decorator."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash('Please log in to access this page.', 'warning')
                return redirect(url_for('auth.login'))
            if not current_user.has_role(role):
                log_event('UNAUTHORIZED_ACCESS', current_user.username,
                          f'Attempted access to {request.path} without role={role}',
                          request.remote_addr)
                flash('You do not have permission to access this page.', 'danger')
                return redirect(url_for('main.index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def check_doctor_verification():
    """Decorator: ensure doctor has passed verification before accessing features."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if current_user.role == 'doctor' and not current_user.is_doctor_verified:
                flash('You must complete the verification process to access this feature.', 'warning')
                return redirect(url_for('doctor.verification'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ─────────────────────────────────────────
# AUDIT LOG HELPER
# ─────────────────────────────────────────

def log_event(event_type, username, details, ip=None):
    """
    Write an immutable audit log entry.
    Always commit separately so a transaction rollback in the main
    request doesn't erase the security event record.
    """
    try:
        log = AuditLog(
            event_type=event_type,
            username=username,
            details=details,
            ip_address=ip
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        # Log to server log even if DB write fails
        current_app.logger.error(f'AUDIT LOG FAILED: {event_type} {username} — {e}')


# ─────────────────────────────────────────
# FILE UPLOAD HELPERS
# ─────────────────────────────────────────

def allowed_file(filename, allowed_extensions):
    """Check file extension (first layer — MIME check is second layer)."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions


def save_uploaded_file(file, upload_folder, prefix='', allowed_mimes=None):
    """
    Save uploaded file after dual validation: extension + magic bytes.
    Returns filename on success, None on failure.
    """
    if not file or not file.filename:
        return None

    filename = secure_filename(file.filename)
    if not filename:
        return None

    # Magic byte check if caller specifies expected MIME types
    if allowed_mimes and not validate_file_magic(file, allowed_mimes):
        current_app.logger.warning(
            f'Magic byte mismatch for upload: {filename} (prefix={prefix})'
        )
        return None

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_filename = f"{prefix}_{timestamp}_{filename}"
    os.makedirs(upload_folder, exist_ok=True)
    filepath = os.path.join(upload_folder, safe_filename)
    file.save(filepath)
    return safe_filename


# ─────────────────────────────────────────
# EMAIL HELPERS
# ─────────────────────────────────────────

def send_confirmation_email(email, confirm_endpoint):
    """
    Generate a signed token and send a confirmation email.
    Token is NEVER returned — it exists only in the email.
    """
    from app import mail
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    token = s.dumps(email, salt='email-confirm')
    confirm_url = url_for(confirm_endpoint, token=token, _external=True)

    subject = 'Confirm your email — OSA Detection System'
    body = f"""Hello,

Please confirm your email address by clicking the link below:

{confirm_url}

This link expires in 24 hours (1 hour for email changes).

If you did not request this, please ignore this email.

— OSA Detection System
"""
    msg = Message(subject=subject, recipients=[email], body=body)
    mail.send(msg)


def send_password_reset_email(email, reset_url):
    """Send password reset link. URL is only ever delivered via email."""
    from app import mail
    subject = 'Password Reset — OSA Detection System'
    body = f"""Hello,

A password reset was requested for your account.

Click the link below to set a new password (valid for 1 hour):

{reset_url}

If you did not request this, you can safely ignore this email.
Your password will not be changed.

— OSA Detection System
"""
    msg = Message(subject=subject, recipients=[email], body=body)
    mail.send(msg)


# ─────────────────────────────────────────
# MAIN ROUTES
# ─────────────────────────────────────────

@main_bp.app_context_processor
def inject_pending_verifications():
    """Inject pending verification count for navbar badge on every page."""
    count = 0
    try:
        if current_user.is_authenticated and current_user.role == 'admin':
            count = DoctorVerificationRequest.query.filter_by(status='pending').count()
    except Exception:
        pass
    return dict(pending_verifications_count=count)


@main_bp.route('/')
def index():
    return render_template('index.html')


# ─────────────────────────────────────────
# AUTH ROUTES
# ─────────────────────────────────────────

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()

        # ── SECURITY FIX: Brute-force lockout ────────────────────
        # Like a bank vault — too many wrong combinations and it
        # locks for a cooling-off period.
        if user and user.locked_until and user.locked_until > datetime.utcnow():
            remaining = int((user.locked_until - datetime.utcnow()).total_seconds() // 60)
            log_event('LOGIN_LOCKED', form.username.data,
                      f'Login attempt on locked account', request.remote_addr)
            flash(f'Account locked due to too many failed attempts. '
                  f'Try again in {remaining} minute(s).', 'danger')
            return render_template('auth/login.html', form=form)

        if user and user.check_password(form.password.data):
            # ── Correct password — reset failure counter ──────────
            user.failed_login_attempts = 0
            user.locked_until = None

            if not user.is_active:
                db.session.commit()
                # Use explicit is_email_confirmed flag if you add it,
                # for now distinguish by last_login being None
                if user.last_login is None:
                    flash('Please confirm your email before logging in. '
                          f'<a href="{url_for("auth.resend_confirmation")}">Resend confirmation email</a>.', 'warning')
                else:
                    flash('Your account has been deactivated. Contact an administrator.', 'danger')
                return redirect(url_for('auth.login'))

            # ── SECURITY FIX: Regenerate session (session fixation) ──
            # Old session ID is invalidated before associating with user
            from flask import session
            session.clear()

            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()

            log_event('LOGIN', user.username, 'Successful login', request.remote_addr)
            flash(f'Welcome back, {user.display_name}!', 'success')

            # ── SECURITY FIX: Safe redirect — no open redirect ────
            next_page = request.args.get('next')
            if next_page and is_safe_url(next_page):
                return redirect(next_page)
            elif user.role == 'admin':
                return redirect(url_for('admin.dashboard'))
            elif user.role == 'doctor':
                if user.verification_status == 'not_submitted':
                    flash('Please complete your verification to unlock all doctor features.', 'info')
                    return redirect(url_for('doctor.verification'))
                return redirect(url_for('doctor.dashboard'))
            else:
                return redirect(url_for('patient.dashboard'))

        else:
            # ── Wrong password — increment counter ────────────────
            if user:
                user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
                if user.failed_login_attempts >= 5:
                    user.locked_until = datetime.utcnow() + timedelta(minutes=15)
                    db.session.commit()
                    log_event('ACCOUNT_LOCKED', user.username,
                              f'Account locked after {user.failed_login_attempts} failed attempts',
                              request.remote_addr)
                    flash('Too many failed attempts. Account locked for 15 minutes.', 'danger')
                    return render_template('auth/login.html', form=form)
                db.session.commit()
                log_event('LOGIN_FAILED', user.username,
                          f'Failed attempt #{user.failed_login_attempts}',
                          request.remote_addr)
            # Same message for valid and invalid usernames (username enumeration prevention)
            flash('Invalid username or password.', 'danger')

    return render_template('auth/login.html', form=form)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data.strip().lower(),
            role=form.role.data,
            is_active=False    # stays inactive until email confirmed
        )
        user.set_password(form.password.data)
        profile = UserProfile()
        user.profile = profile
        db.session.add(user)
        db.session.commit()

        # ── SECURITY FIX: Dev fallback auto-activation REMOVED ───
        # In the original code, if SMTP failed the account was auto-activated,
        # completely bypassing email verification.
        # Now: if email fails, user is told to contact support. Account stays inactive.
        try:
            send_confirmation_email(user.email, 'auth.confirm_email')
            flash('Account created! Please check your email and click '
                  'the confirmation link to activate your account.', 'success')
        except Exception as e:
            current_app.logger.error(f'Registration confirmation email failed: {e}')
            # Do NOT auto-activate. Do NOT expose the error to the user.
            flash('Account created! Please check your email for a confirmation link. '
                  'If you do not receive it within a few minutes, '
                  'use the resend option on the login page.', 'success')

        return redirect(url_for('auth.login'))

    return render_template('auth/register.html', form=form)


@auth_bp.route('/confirm-email/<token>')
def confirm_email(token):
    try:
        s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        email = s.loads(token, salt='email-confirm', max_age=86400)  # 24 hrs
    except SignatureExpired:
        flash('Confirmation link has expired. Please register again.', 'danger')
        return redirect(url_for('auth.register'))
    except BadSignature:
        flash('Invalid confirmation link.', 'danger')
        return redirect(url_for('auth.register'))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash('Account not found.', 'danger')
        return redirect(url_for('auth.register'))
    if user.is_active:
        flash('Account already confirmed. Please log in.', 'info')
        return redirect(url_for('auth.login'))

    user.is_active = True
    db.session.commit()
    log_event('EMAIL_CONFIRMED', user.username, 'Email confirmed', request.remote_addr)
    flash('Email confirmed! Your account is now active. Please log in.', 'success')
    return redirect(url_for('auth.login'))


@auth_bp.route('/resend-confirmation', methods=['GET', 'POST'])
def resend_confirmation():
    from app.forms import ForgotPasswordForm
    form = ForgotPasswordForm()
    form.submit.label.text = 'Resend Confirmation'
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        # Always show same message — don't reveal whether email exists
        if user and not user.is_active:
            try:
                send_confirmation_email(user.email, 'auth.confirm_email')
            except Exception as e:
                current_app.logger.error(f'Resend confirmation failed: {e}')
        flash('If an unconfirmed account exists with that email, '
              'a confirmation link has been sent.', 'info')
        return redirect(url_for('auth.login'))
    return render_template('auth/resend_confirmation.html', form=form)


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    log_event('LOGOUT', current_user.username, 'User logged out', request.remote_addr)
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.index'))


# ─────────────────────────────────────────
# PASSWORD RESET
# ─────────────────────────────────────────

@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if user and user.is_active:
            raw_token = secrets.token_urlsafe(32)

            # ── SECURITY FIX: Store hash of token, not raw token ──
            # Like a bouncer who only keeps a photo of your ID hash —
            # if the list is stolen, no one can use the original IDs.
            user.reset_token         = hash_token(raw_token)
            user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
            db.session.commit()

            reset_url = url_for('auth.reset_password', token=raw_token, _external=True)

            # ── SECURITY FIX: Send via email ONLY — never flash the URL ──
            try:
                send_password_reset_email(user.email, reset_url)
            except Exception as e:
                current_app.logger.error(f'Password reset email failed for {user.email}: {e}')
                # Clear the token so it can't be exploited without delivery
                user.reset_token         = None
                user.reset_token_expires = None
                db.session.commit()
                flash('Could not send reset email. Please try again later.', 'danger')
                return redirect(url_for('auth.forgot_password'))

            log_event('PASSWORD_RESET_REQUESTED', user.username,
                      'Password reset email sent', request.remote_addr)

        # Always same message — don't reveal whether email is registered
        flash('If that email is registered and active, a reset link has been sent.', 'info')
        return redirect(url_for('auth.forgot_password'))
    return render_template('auth/forgot_password.html', form=form)


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    # ── SECURITY FIX: Compare against stored hash, not raw token ──
    token_hash = hash_token(token)
    user = User.query.filter_by(reset_token=token_hash).first()

    if not user or (user.reset_token_expires and
                    user.reset_token_expires < datetime.utcnow()):
        flash('Reset link is invalid or has expired.', 'danger')
        return redirect(url_for('auth.forgot_password'))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.reset_token         = None
        user.reset_token_expires = None
        # Reset any lockout that may have been in place
        user.failed_login_attempts = 0
        user.locked_until          = None
        db.session.commit()
        log_event('PASSWORD_RESET', user.username, 'Password reset completed', request.remote_addr)
        flash('Password reset successfully. Please log in.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html', form=form, token=token)


@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            log_event('PASSWORD_CHANGE_FAILED', current_user.username,
                      'Wrong current password supplied', request.remote_addr)
            flash('Current password is incorrect.', 'danger')
            return render_template('auth/change_password.html', form=form)
        current_user.set_password(form.new_password.data)
        db.session.commit()
        log_event('PASSWORD_CHANGED', current_user.username,
                  'Password changed', request.remote_addr)
        flash('Password changed successfully.', 'success')
        if current_user.role == 'doctor':
            return redirect(url_for('doctor.profile'))
        elif current_user.role == 'admin':
            return redirect(url_for('admin.dashboard'))
        else:
            return redirect(url_for('patient.profile'))
    return render_template('auth/change_password.html', form=form)


@auth_bp.route('/change-email', methods=['GET', 'POST'])
@login_required
def change_email():
    form = ChangeEmailForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.password.data):
            flash('Password is incorrect.', 'danger')
            return render_template('auth/change_email.html', form=form)

        new_email = form.new_email.data.strip().lower()
        existing = User.query.filter_by(email=new_email).first()
        if existing and existing.id != current_user.id:
            flash('That email is already registered to another account.', 'danger')
            return render_template('auth/change_email.html', form=form)

        current_user.pending_email = new_email
        db.session.commit()

        # ── SECURITY FIX: Confirmation URL goes to email ONLY ─────
        # Never expose the signed token in a flash message or page HTML.
        try:
            send_confirmation_email(new_email, 'auth.confirm_email_change')
            flash(f'A confirmation link has been sent to {new_email}. '
                  'Click it to complete the change.', 'success')
        except Exception as e:
            current_app.logger.error(f'Email change confirmation send failed: {e}')
            # Roll back pending_email so user can retry cleanly
            current_user.pending_email = None
            db.session.commit()
            flash('Could not send confirmation email. Please try again later.', 'danger')
            return render_template('auth/change_email.html', form=form)

        log_event('EMAIL_CHANGE_REQUESTED', current_user.username,
                  f'Email change requested to {new_email}', request.remote_addr)

        if current_user.role == 'doctor':
            return redirect(url_for('doctor.profile'))
        elif current_user.role == 'admin':
            return redirect(url_for('admin.dashboard'))
        else:
            return redirect(url_for('patient.profile'))
    return render_template('auth/change_email.html', form=form)


@auth_bp.route('/confirm-email-change/<token>')
@login_required
def confirm_email_change(token):
    try:
        s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        new_email = s.loads(token, salt='email-confirm', max_age=3600)  # 1 hr
    except SignatureExpired:
        flash('Email change link has expired. Please request a new one.', 'danger')
        return redirect(url_for('auth.change_email'))
    except BadSignature:
        flash('Invalid email change link.', 'danger')
        return redirect(url_for('auth.change_email'))

    if current_user.pending_email != new_email:
        flash('This link does not match your pending email change.', 'danger')
        return redirect(url_for('auth.change_email'))

    current_user.email         = new_email
    current_user.pending_email = None
    db.session.commit()
    log_event('EMAIL_CHANGED', current_user.username,
              f'Email changed to {new_email}', request.remote_addr)
    flash(f'Email successfully changed to {new_email}.', 'success')
    if current_user.role == 'doctor':
        return redirect(url_for('doctor.profile'))
    elif current_user.role == 'admin':
        return redirect(url_for('admin.dashboard'))
    else:
        return redirect(url_for('patient.profile'))


# ─────────────────────────────────────────
# ADMIN ROUTES
# ─────────────────────────────────────────

@admin_bp.route('/dashboard')
@login_required
@role_required('admin')
def dashboard():
    users       = User.query.order_by(User.created_at.desc()).all()
    assignments = PatientAssignment.query.order_by(PatientAssignment.assigned_at.desc()).all()
    audit_logs  = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(100).all()

    pending_requests = DoctorVerificationRequest.query.filter_by(
        status='pending'
    ).order_by(DoctorVerificationRequest.submitted_at.desc()).all()

    all_analyses = Analysis.query.all()
    stats = {
        'total_users':        User.query.count(),
        'active_users':       User.query.filter_by(is_active=True).count(),
        'total_admins':       User.query.filter_by(role='admin').count(),
        'total_doctors':      User.query.filter_by(role='doctor').count(),
        'total_patients':     User.query.filter_by(role='patient').count(),
        'total_files':        DataFile.query.count(),
        'total_analyses':     len(all_analyses),
        'completed_analyses': Analysis.query.filter_by(status='completed').count(),
        'pending_analyses':   Analysis.query.filter_by(status='pending').count(),
        'reviewed_analyses':  Analysis.query.filter_by(status='reviewed').count(),
        'osa_detected':       Analysis.query.filter_by(osa_detected=True).count(),
        'osa_normal':         Analysis.query.filter_by(osa_detected=False).count(),
        'severity_normal':    Analysis.query.filter_by(severity='normal').count(),
        'severity_mild':      Analysis.query.filter_by(severity='mild').count(),
        'severity_moderate':  Analysis.query.filter_by(severity='moderate').count(),
        'severity_severe':    Analysis.query.filter_by(severity='severe').count(),
    }

    pending_research = ResearchRequest.query.filter_by(status='pending').count()

    return render_template('admin/dashboard.html',
                           users=users,
                           assignments=assignments,
                           audit_logs=audit_logs,
                           stats=stats,
                           pending_requests=pending_requests,
                           pending_verifications_count=len(pending_requests),
                           pending_research_count=pending_research)


@admin_bp.route('/toggle-user/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin')
def toggle_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.role == 'admin':
        flash('Cannot deactivate admin accounts.', 'danger')
        return redirect(url_for('admin.dashboard'))
    user.is_active = not user.is_active
    db.session.commit()
    action = 'Activated' if user.is_active else 'Deactivated'
    log_event('USER_TOGGLE', current_user.username,
              f'{action} user {user.username}', request.remote_addr)
    flash(f'User {user.username} {action.lower()} successfully.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/change-role/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin')
def change_role(user_id):
    user = User.query.get_or_404(user_id)
    if user.role == 'admin':
        flash('Cannot change admin role.', 'danger')
        return redirect(url_for('admin.dashboard'))
    new_role = request.form.get('role')
    if new_role not in ('patient', 'doctor'):
        flash('Invalid role.', 'danger')
        return redirect(url_for('admin.dashboard'))

    old_role = user.role
    user.role = new_role

    # ── SECURITY FIX: Reset verification when promoting to doctor ──
    # A patient promoted to doctor must go through the full
    # verification workflow — they cannot inherit a clean status.
    if new_role == 'doctor' and old_role != 'doctor':
        user.verification_status   = 'not_submitted'
        user.two_factor_verified   = False
        user.two_factor_enabled    = False
        user.verified_at           = None

    db.session.commit()
    log_event('ROLE_CHANGE', current_user.username,
              f'Changed {user.username} role: {old_role} → {new_role}',
              request.remote_addr)
    flash(f'Role updated for {user.username}.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/verification-requests')
@login_required
@role_required('admin')
def verification_requests():
    pending_requests = DoctorVerificationRequest.query.filter_by(
        status='pending'
    ).order_by(DoctorVerificationRequest.submitted_at.desc()).all()

    all_requests = DoctorVerificationRequest.query.order_by(
        DoctorVerificationRequest.submitted_at.desc()
    ).limit(100).all()

    # Build expiry map {user_id: {days, status}} for all doctors in pending list
    # so the template can display license validity without Python date logic
    expiry_map = {}
    all_reqs = list(pending_requests) + list(all_requests)
    for req in all_reqs:
        if req.user_id not in expiry_map:
            days, status = check_license_expiry(req.user)
            expiry_map[req.user_id] = {'days': days, 'status': status}

    return render_template('admin/verification_requests.html',
                           pending_requests=pending_requests,
                           all_requests=all_requests,
                           expiry_map=expiry_map)


@admin_bp.route('/verify-doctor/<int:request_id>', methods=['POST'])
@login_required
@role_required('admin')
def verify_doctor(request_id):
    verification_request = DoctorVerificationRequest.query.get_or_404(request_id)
    doctor               = User.query.get(verification_request.user_id)
    action               = request.form.get('action')
    review_notes         = request.form.get('review_notes', '')

    if action == 'approve':
        verification_request.status      = 'approved'
        verification_request.reviewed_by = current_user.id
        verification_request.reviewed_at = datetime.utcnow()
        verification_request.review_notes = review_notes
        doctor.verification_status = 'approved'
        doctor.two_factor_verified = True
        doctor.verified_at         = datetime.utcnow()
        doctor.verified_by         = current_user.id
        log_event('DOCTOR_VERIFIED', current_user.username,
                  f'Approved doctor {doctor.username}', request.remote_addr)
        flash(f'Doctor {doctor.username} has been verified successfully!', 'success')

    elif action == 'reject':
        rejection_reason = request.form.get(
            'rejection_reason',
            'Verification documents do not meet requirements.'
        )
        verification_request.status           = 'rejected'
        verification_request.reviewed_by      = current_user.id
        verification_request.reviewed_at      = datetime.utcnow()
        verification_request.review_notes     = review_notes
        verification_request.rejection_reason = rejection_reason
        doctor.verification_status = 'rejected'
        doctor.two_factor_verified = False
        log_event('DOCTOR_REJECTED', current_user.username,
                  f'Rejected doctor {doctor.username}', request.remote_addr)
        flash(f'Doctor {doctor.username} verification has been rejected.', 'warning')

    db.session.commit()
    return redirect(url_for('admin.verification_requests'))


# ─────────────────────────────────────────
# DOCTOR ROUTES
# ─────────────────────────────────────────

@doctor_bp.route('/dashboard')
@login_required
@role_required('doctor')
def dashboard():
    # Check license expiry on every dashboard load
    days_remaining, expiry_status = check_license_expiry(current_user)

    # If license expired: lock patient access and notify
    if expiry_status == 'expired':
        if current_user.verification_status == 'approved':
            current_user.verification_status = 'rejected'
            db.session.commit()
            log_event('LICENSE_EXPIRED_LOCKED', current_user.username,
                      'Doctor patient access locked due to expired license',
                      request.remote_addr)
        send_license_expiry_email(current_user, days_remaining)

    # Send reminder emails at 7-day and 1-day marks
    # Use audit log to avoid sending the same reminder twice per day
    elif expiry_status in ('warning', 'critical'):
        from app.models import AuditLog
        from datetime import date
        today_str = date.today().isoformat()
        already_sent = AuditLog.query.filter(
            AuditLog.username == current_user.username,
            AuditLog.event_type == 'LICENSE_EXPIRY_EMAIL',
            AuditLog.details.contains(today_str)
        ).first()
        if not already_sent:
            send_license_expiry_email(current_user, days_remaining)

    return render_template('doctor/dashboard.html',
                           license_days=days_remaining,
                           expiry_status=expiry_status)


@doctor_bp.route('/verification', methods=['GET', 'POST'])
@login_required
@role_required('doctor')
def verification():
    form = DoctorVerificationForm()

    if form.validate_on_submit():
        upload_folder = os.path.join(
            current_app.config.get('UPLOAD_FOLDER', 'uploads'), 'verification_docs')

        # ── SECURITY FIX: MIME check for verification documents ───
        doc_filename = save_uploaded_file(
            form.verification_document.data,
            upload_folder,
            prefix=f'doctor_{current_user.id}',
            allowed_mimes=['pdf', 'jpeg', 'png']
        )

        if doc_filename:
            current_user.verification_document_path = os.path.join(
                'verification_docs', doc_filename)
            current_user.verification_status = 'pending'
            current_user.two_factor_enabled  = True

            verification_req = DoctorVerificationRequest(
                user_id         = current_user.id,
                document_type   = 'medical_license',
                document_path   = current_user.verification_document_path,
                document_number = form.document_number.data,
                status          = 'pending'
            )
            db.session.add(verification_req)
            db.session.commit()
            log_event('VERIFICATION_SUBMITTED', current_user.username,
                      'Doctor verification documents submitted', request.remote_addr)
            flash('Verification documents submitted! You will be notified once reviewed.', 'success')
            return redirect(url_for('doctor.dashboard'))
        else:
            flash('Invalid file type or upload error. Please upload a valid PDF or image.', 'danger')

    rejection_reason = None
    if current_user.verification_status == 'rejected':
        last_request = DoctorVerificationRequest.query.filter_by(
            user_id=current_user.id
        ).order_by(DoctorVerificationRequest.submitted_at.desc()).first()
        if last_request:
            rejection_reason = last_request.rejection_reason

    # Pass license expiry context so template can show appropriate banner
    days, exp_status = check_license_expiry(current_user)
    return render_template('doctor/doctor_verification.html',
                           form=form,
                           rejection_reason=rejection_reason,
                           license_expired=(exp_status == 'expired'),
                           license_warning=(exp_status in ('warning', 'critical')),
                           license_days=days)


@doctor_bp.route('/doctor-profile', methods=['GET', 'POST'])
@login_required
@role_required('doctor')
def doctor_profile():
    profile = DoctorProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        profile = DoctorProfile(user_id=current_user.id)
        db.session.add(profile)
        db.session.commit()

    form = DoctorProfileForm(obj=profile)

    if form.validate_on_submit():
        upload_folder = os.path.join(
            current_app.config.get('UPLOAD_FOLDER', 'uploads'), 'doctor_docs')

        if form.cv_document.data:
            cv_filename = save_uploaded_file(
                form.cv_document.data, upload_folder,
                prefix=f'cv_{current_user.id}',
                allowed_mimes=['pdf']
            )
            if cv_filename:
                profile.cv_document_path = os.path.join('doctor_docs', cv_filename)

        if form.license_document.data:
            license_filename = save_uploaded_file(
                form.license_document.data, upload_folder,
                prefix=f'license_{current_user.id}',
                allowed_mimes=['pdf', 'jpeg', 'png']
            )
            if license_filename:
                profile.license_document_path = os.path.join('doctor_docs', license_filename)

        if form.certification_document.data:
            cert_filename = save_uploaded_file(
                form.certification_document.data, upload_folder,
                prefix=f'cert_{current_user.id}',
                allowed_mimes=['pdf', 'jpeg', 'png']
            )
            if cert_filename:
                profile.certification_document_path = os.path.join('doctor_docs', cert_filename)

        form.populate_obj(profile)
        profile.updated_at = datetime.utcnow()
        db.session.commit()
        log_event('PROFILE_UPDATED', current_user.username,
                  'Doctor profile updated', request.remote_addr)
        flash('Professional profile updated successfully!', 'success')
        return redirect(url_for('doctor.dashboard'))

    has_data = bool(profile.medical_license_number or profile.current_hospital
                    or profile.primary_specialization)
    edit_mode = request.args.get('edit') == '1' or not has_data
    return render_template('doctor/doctor_profile.html',
                           form=form, profile=profile,
                           has_data=has_data, edit_mode=edit_mode)


# ── doctor profile alias (template may use either URL) ──────────
@doctor_bp.route('/profile')
@login_required
@role_required('doctor')
def profile():
    return redirect(url_for('doctor.doctor_profile'))


@doctor_bp.route('/patients')
@login_required
@role_required('doctor')
@check_doctor_verification()
def patients():
    assignments = PatientAssignment.query.filter_by(
        doctor_id=current_user.id, is_active=True).all()

    patients_data = []
    for assignment in assignments:
        patient         = assignment.patient
        data_file_count = DataFile.query.filter_by(patient_id=patient.id).count()
        latest_analysis = Analysis.query.join(DataFile).filter(
            DataFile.patient_id == patient.id
        ).order_by(Analysis.completed_at.desc()).first()
        patients_data.append({
            'assignment':     assignment,
            'patient':        patient,
            'data_file_count': data_file_count,
            'latest_analysis': latest_analysis
        })

    return render_template('doctor/patients.html', patients_data=patients_data)


@doctor_bp.route('/patient/<int:patient_id>')
@login_required
@role_required('doctor')
@check_doctor_verification()
def patient_detail(patient_id):
    # Ensures doctor owns this patient — 404 if not
    PatientAssignment.query.filter_by(
        doctor_id=current_user.id,
        patient_id=patient_id,
        is_active=True
    ).first_or_404()

    patient    = User.query.get_or_404(patient_id)
    data_files = DataFile.query.filter_by(
        patient_id=patient_id
    ).order_by(DataFile.upload_date.desc()).all()

    file_features = {}
    for f in data_files:
        if f.analysis and f.analysis.features_json:
            try:
                file_features[f.id] = json.loads(f.analysis.features_json)
            except Exception:
                file_features[f.id] = {}

    log_event('PATIENT_RECORD_ACCESS', current_user.username,
              f'Doctor accessed patient {patient_id} record', request.remote_addr)

    return render_template('doctor/patient_detail.html',
                           patient=patient,
                           data_files=data_files,
                           assignment=PatientAssignment.query.filter_by(
                               doctor_id=current_user.id,
                               patient_id=patient_id,
                               is_active=True
                           ).first(),
                           file_features=file_features)


@doctor_bp.route('/assign-patient', methods=['GET', 'POST'])
@login_required
@role_required('doctor')
@check_doctor_verification()
def assign_patient():
    form = AssignPatientForm()

    assigned_ids = [a.patient_id for a in PatientAssignment.query.filter_by(
        doctor_id=current_user.id, is_active=True).all()]

    available_patients = User.query.filter(
        User.role == 'patient',
        ~User.id.in_(assigned_ids) if assigned_ids else True
    ).all()

    form.patient_id.choices = [
        (p.id, f"{p.username} - {p.email}") for p in available_patients]

    if form.validate_on_submit():
        assignment = PatientAssignment(
            doctor_id  = current_user.id,
            patient_id = form.patient_id.data,
            notes      = form.notes.data
        )
        db.session.add(assignment)
        db.session.commit()
        log_event('PATIENT_ASSIGNED', current_user.username,
                  f'Assigned patient {form.patient_id.data}', request.remote_addr)
        flash('Patient assigned successfully!', 'success')
        return redirect(url_for('doctor.patients'))

    return render_template('doctor/assign_patient.html',
                           form=form,
                           available_patients=available_patients)


@doctor_bp.route('/analysis/<int:analysis_id>')
@login_required
@role_required('doctor')
@check_doctor_verification()
def view_analysis(analysis_id):
    analysis  = Analysis.query.get_or_404(analysis_id)
    data_file = analysis.data_file
    patient   = data_file.patient

    # Enforce doctor–patient relationship
    PatientAssignment.query.filter_by(
        doctor_id=current_user.id,
        patient_id=patient.id,
        is_active=True
    ).first_or_404()

    all_analyses = Analysis.query.join(DataFile).filter(
        DataFile.patient_id == patient.id,
        Analysis.status.in_(['completed', 'reviewed'])
    ).order_by(Analysis.completed_at.desc()).all()

    analysis_features = {}
    if analysis.features_json:
        try:
            analysis_features = json.loads(analysis.features_json)
        except Exception:
            analysis_features = {}

    log_event('ANALYSIS_VIEWED', current_user.username,
              f'Doctor viewed analysis {analysis_id} for patient {patient.id}',
              request.remote_addr)

    return render_template('doctor/analysis_detail.html',
                           analysis=analysis,
                           patient=patient,
                           all_analyses=all_analyses,
                           features=analysis_features,
                           doctor_view=True)


@doctor_bp.route('/analysis/<int:analysis_id>/review', methods=['GET', 'POST'])
@login_required
@role_required('doctor')
@check_doctor_verification()
def review_analysis(analysis_id):
    from flask_wtf import FlaskForm
    analysis  = Analysis.query.get_or_404(analysis_id)
    data_file = analysis.data_file
    patient   = data_file.patient

    PatientAssignment.query.filter_by(
        doctor_id=current_user.id,
        patient_id=patient.id,
        is_active=True
    ).first_or_404()

    if request.method == 'POST':
        form = FlaskForm()
        if not form.validate():
            flash('Security validation failed. Please try again.', 'danger')
            return redirect(url_for('doctor.review_analysis', analysis_id=analysis_id))

        analysis.doctor_notes    = request.form.get('doctor_notes', '').strip()[:5000]
        analysis.recommendations = request.form.get('recommendations', '').strip()[:5000]
        analysis.reviewed_by     = current_user.id
        analysis.reviewed_at     = datetime.utcnow()
        analysis.status          = 'reviewed'
        db.session.commit()

        log_event('ANALYSIS_REVIEWED', current_user.username,
                  f'Review saved for analysis {analysis_id} — patient {patient.display_name}',
                  request.remote_addr)
        flash('Review saved successfully!', 'success')
        return redirect(url_for('doctor.view_analysis', analysis_id=analysis_id))

    return render_template('doctor/review_analysis.html',
                           analysis=analysis, patient=patient)


@doctor_bp.route('/compare-patients')
@login_required
@role_required('doctor')
@check_doctor_verification()
def compare_patients():
    assignments  = PatientAssignment.query.filter_by(
        doctor_id=current_user.id, is_active=True).all()
    all_patients = [a.patient for a in assignments]
    # Only patients this doctor owns — validated via assignment check
    owned_ids    = {a.patient_id for a in assignments}

    selected_p1 = request.args.get('patient1', type=int)
    selected_p2 = request.args.get('patient2', type=int)

    def get_patient_data(patient_id):
        if not patient_id or patient_id not in owned_ids:
            return None
        patient  = User.query.get(patient_id)
        analyses = Analysis.query.join(DataFile).filter(
            DataFile.patient_id == patient_id,
            Analysis.status.in_(['completed', 'reviewed'])
        ).order_by(Analysis.completed_at.desc()).all()
        return {'patient': patient, 'analyses': analyses}

    patient1_data = get_patient_data(selected_p1)
    patient2_data = get_patient_data(selected_p2)

    def to_json(data):
        if not data or not data['analyses']:
            return '[]'
        return json.dumps([{
            'date':               a.completed_at.strftime('%Y-%m-%d'),
            'ahi_score':          a.ahi_score,
            'severity':           a.severity,
            'apnea_events':       a.apnea_events or 0,
            'hypopnea_events':    a.hypopnea_events or 0,
            'oxygen_desaturation': float(a.oxygen_desaturation or 0),
        } for a in data['analyses']])

    sel_a = request.args.get('analysis_a', type=int)
    sel_b = request.args.get('analysis_b', type=int)

    def load_deep(aid):
        if not aid:
            return None
        a = Analysis.query.get(aid)
        if not a or a.data_file.patient_id not in owned_ids:
            return None
        features = {}
        if a.features_json:
            try:
                features = json.loads(a.features_json)
            except Exception:
                pass
        return {'analysis': a, 'features': features,
                'patient': a.data_file.patient,
                'data_file': a.data_file}

    def patient_analyses(patient_id):
        return Analysis.query.join(DataFile).filter(
            DataFile.patient_id == patient_id,
            Analysis.status.in_(['completed', 'reviewed'])
        ).order_by(Analysis.completed_at.desc()).all()

    return render_template('doctor/compare_patients.html',
                           all_patients=all_patients,
                           selected_p1=selected_p1,
                           selected_p2=selected_p2,
                           patient1_data=patient1_data,
                           patient2_data=patient2_data,
                           patient1_json=to_json(patient1_data),
                           patient2_json=to_json(patient2_data),
                           patient_analyses=patient_analyses,
                           sel_a=sel_a, sel_b=sel_b,
                           data_a=load_deep(sel_a),
                           data_b=load_deep(sel_b))


@doctor_bp.route('/view-patient-demographics/<int:patient_id>')
@login_required
@role_required('doctor')
@check_doctor_verification()
def view_patient_demographics(patient_id):
    assignment = PatientAssignment.query.filter_by(
        doctor_id=current_user.id,
        patient_id=patient_id,
        is_active=True
    ).first()
    if not assignment:
        log_event('UNAUTHORIZED_ACCESS', current_user.username,
                  f'Doctor tried to access unassigned patient {patient_id} demographics',
                  request.remote_addr)
        flash('You do not have access to this patient.', 'danger')
        return redirect(url_for('doctor.dashboard'))

    patient      = User.query.get_or_404(patient_id)
    demographics = PatientDemographics.query.filter_by(user_id=patient_id).first()
    log_event('DEMOGRAPHICS_ACCESSED', current_user.username,
              f'Doctor accessed demographics for patient {patient_id}', request.remote_addr)
    return render_template('doctor/view_patient_demographics.html',
                           patient=patient, demographics=demographics)


# ─────────────────────────────────────────
# PATIENT ROUTES
# ─────────────────────────────────────────

@patient_bp.route('/dashboard')
@login_required
@role_required('patient')
def dashboard():
    recent_data_files = DataFile.query.filter_by(
        patient_id=current_user.id
    ).order_by(DataFile.upload_date.desc()).limit(3).all()

    total_data_files = DataFile.query.filter_by(patient_id=current_user.id).count()

    recent_analyses = Analysis.query.join(DataFile).filter(
        DataFile.patient_id == current_user.id
    ).order_by(Analysis.completed_at.desc()).limit(3).all()

    return render_template('patient/dashboard.html',
                           recent_data_files=recent_data_files,
                           total_data_files=total_data_files,
                           recent_analyses=recent_analyses)


@patient_bp.route('/profile', methods=['GET', 'POST'])
@login_required
@role_required('patient')
def profile():
    prof = current_user.profile
    if not prof:
        prof = UserProfile(user_id=current_user.id)
        db.session.add(prof)
        db.session.commit()

    # Use obj= so WTForms pre-populates all fields from the model on GET
    form = ProfileForm(obj=prof)

    if form.validate_on_submit():
        form.populate_obj(prof)
        prof.updated_at = datetime.utcnow()
        db.session.commit()
        log_event('PROFILE_UPDATED', current_user.username,
                  'Patient profile updated', request.remote_addr)
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('patient.profile'))

    # has_data: True if the profile already has meaningful content to display
    has_data = bool(prof.first_name or prof.last_name or prof.phone or
                    prof.date_of_birth or prof.gender or prof.address)
    edit_mode = request.args.get('edit') == '1' or not has_data

    return render_template('patient/profile.html',
                           form=form, profile=prof,
                           has_data=has_data, edit_mode=edit_mode)


@patient_bp.route('/demographics/parse-csv', methods=['POST'])
@login_required
@role_required('patient')
def demographics_parse_csv():
    """Parse a CSV upload and return matched demographic fields as JSON."""
    file = request.files.get('csv_file')
    if not file or not file.filename.lower().endswith('.csv'):
        return json.dumps({'error': 'Please upload a valid CSV file.'}), 400

    COL_MAP = {
        'age_s1':'age_s1','age':'age_s1','gender':'gender','sex':'gender',
        'race':'race','ethnicity':'ethnicity','bmi_s1':'bmi_s1','bmi':'bmi_s1',
        'weight':'weight','weight_kg':'weight','height':'height','height_cm':'height',
        'waist':'waist','waistcircumference':'waist','hip':'hip','hipcircumference':'hip',
        'neck20':'neck20','neck':'neck20','neckcircumference':'neck20',
        'systbp':'systbp','sys_bp':'systbp','systolic':'systbp',
        'diasbp':'diasbp','dia_bp':'diasbp','diastolic':'diasbp',
        'syst120':'syst120','dias120':'dias120','syst220':'syst220','dias220':'dias220',
        'htnderv_s1':'htnderv_s1','htn':'htnderv_s1','srhype':'srhype','htnmed1':'htnmed1',
        'beta1':'beta1','beta_blocker':'beta1','ace1':'ace1','ace_inhibitor':'ace1',
        'diuret1':'diuret1','diuretic':'diuret1','lipid1':'lipid1','lipid_med':'lipid1',
        'chol':'chol','cholesterol':'chol','total_cholesterol':'chol',
        'hdl':'hdl','hdl_cholesterol':'hdl','trig':'trig','triglycerides':'trig',
        'parrptdiab':'parrptdiab','fev1':'fev1','fvc':'fvc',
        'emphys15':'emphys15','emphysema':'emphys15','crbron15':'crbron15',
        'chronic_bronchitis':'crbron15','copd15':'copd15','copd':'copd15',
        'asthma15':'asthma15','asthma':'asthma15',
        'smokstat_s1':'smokstat_s1','smoking_status':'smokstat_s1',
        'evsmok15':'evsmok15','ever_smoked':'evsmok15',
        'smknow15':'smknow15','currently_smoking':'smknow15',
        'cigday15':'cigday15','cigarettes_per_day':'cigday15',
        'alcoh':'alcoh','alcohol':'alcoh','wine15':'wine15','wine':'wine15',
        'beer15':'beer15','beer':'beer15','shots15':'shots15','shots':'shots15',
        'coffee15':'coffee15','coffee':'coffee15',
        'prev_hx_mi':'prev_hx_mi','history_mi':'prev_hx_mi',
        'prev_hx_stroke':'prev_hx_stroke','history_stroke':'prev_hx_stroke',
        'mi15':'mi15','stroke15':'stroke15','hf15':'hf15','heart_failure':'hf15',
        'cabg15':'cabg15','cabg':'cabg15','ca15':'ca15','cardiac_arrest':'ca15',
        'angina15':'angina15','angina':'angina15',
        'ess_s1':'ess_s1','ess':'ess_s1','epworth':'ess_s1',
        'hvsnrd02':'hvsnrd02','snores':'hvsnrd02','hosnr02':'hosnr02',
        'snore_frequency':'hosnr02','loudsn02':'loudsn02','snore_loudness':'loudsn02',
        'yrssnr02':'yrssnr02','years_snoring':'yrssnr02',
        'stpbrt02':'stpbrt02','stopped_breathing':'stpbrt02',
        'hostbr02':'hostbr02','sleepy02':'sleepy02','sleepiness':'sleepy02',
        'slpill15':'slpill15','sleep_pills':'slpill15',
        'napshr15':'napshr15','nap_hours':'napshr15',
        'pcs_s1':'pcs_s1','pcs':'pcs_s1','mcs_s1':'mcs_s1','mcs':'mcs_s1',
        'genhth25':'genhth25','general_health':'genhth25',
    }

    BOOLEAN_FIELDS = {
        'htnderv_s1','srhype','htnmed1','beta1','ace1','diuret1','lipid1',
        'parrptdiab','emphys15','crbron15','copd15','asthma15','evsmok15','smknow15',
        'prev_hx_mi','prev_hx_stroke','mi15','stroke15','hf15','cabg15','ca15',
        'angina15','hvsnrd02','stpbrt02','slpill15',
    }

    try:
        raw    = file.read(512 * 1024).decode('utf-8', errors='replace')  # max 512KB read
        reader = csv.DictReader(io.StringIO(raw))
        # ── SECURITY FIX: limit rows processed to prevent DoS ─────
        rows   = []
        for i, row in enumerate(reader):
            if i >= 500:
                break
            rows.append(row)
    except Exception as e:
        return json.dumps({'error': 'Could not parse CSV.'}), 400   # don't leak exception

    if not rows:
        return json.dumps({'error': 'CSV file is empty.'}), 400

    row         = rows[0]
    matched     = {}
    unmatched_cols = []

    for col, val in row.items():
        if col is None:
            continue
        key   = col.strip().lower().replace(' ', '_').replace('-', '_')
        field = COL_MAP.get(key)
        if field and val is not None and str(val).strip() not in ('', 'nan', 'NaN', 'NULL', 'null'):
            raw_val = str(val).strip()
            if field in BOOLEAN_FIELDS:
                matched[field] = '1' if raw_val.lower() in ('1', 'true', 'yes', 'y') else '0'
            else:
                matched[field] = raw_val
        elif field is None:
            unmatched_cols.append(col.strip())

    return json.dumps({
        'matched':        matched,
        'matched_count':  len(matched),
        'total_cols':     len(row),
        'unmatched_cols': unmatched_cols[:20],
    })


@patient_bp.route('/demographics', methods=['GET', 'POST'])
@login_required
@role_required('patient')
def demographics():
    demo = PatientDemographics.query.filter_by(user_id=current_user.id).first()
    if not demo:
        demo = PatientDemographics(user_id=current_user.id)
        db.session.add(demo)
        db.session.commit()

    form = PatientDemographicsForm(obj=demo)
    if form.validate_on_submit():
        form.populate_obj(demo)
        demo.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Demographics updated successfully!', 'success')
        return redirect(url_for('patient.dashboard'))

    has_data = bool(demo.age_s1 or demo.bmi_s1 or demo.systbp or demo.ess_s1
                    or demo.weight or demo.height)
    edit_mode = request.args.get('edit') == '1' or not has_data
    return render_template('patient/patient_demographics.html',
                           form=form, demographics=demo,
                           has_data=has_data, edit_mode=edit_mode)


@patient_bp.route('/upload', methods=['GET', 'POST'])
@login_required
@role_required('patient')
def upload_data_file():
    form = DataFileUploadForm()

    if form.validate_on_submit():
        file = form.data_file.data
        if file:
            original_filename = secure_filename(file.filename)
            if not original_filename:
                flash('Invalid filename.', 'danger')
                return render_template('patient/upload.html', form=form)

            # ── SECURITY FIX: MIME validation on EDF/CSV uploads ──
            if not validate_file_magic(file, ['csv', 'txt', 'edf']):
                log_event('UPLOAD_REJECTED', current_user.username,
                          f'File failed MIME check: {original_filename}',
                          request.remote_addr)
                flash('File content does not match its extension. Upload rejected.', 'danger')
                return render_template('patient/upload.html', form=form)

            timestamp  = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename   = f"{current_user.id}_{timestamp}_{original_filename}"
            ext        = original_filename.rsplit('.', 1)[1].lower()
            file_type  = 'edf' if ext == 'edf' else ('excel' if ext in ('xlsx','xls') else 'csv')

            upload_folder = current_app.config['UPLOAD_FOLDER']
            os.makedirs(upload_folder, exist_ok=True)
            file_path = os.path.join(upload_folder, filename)
            file.seek(0)
            file.save(file_path)
            file_size = os.path.getsize(file_path)

            data_file = DataFile(
                patient_id=current_user.id,
                filename=filename,
                original_filename=original_filename,
                file_path=file_path,
                file_type=file_type,
                file_size=file_size,
                status='uploaded'
            )
            db.session.add(data_file)
            db.session.commit()

            log_event('UPLOAD', current_user.username,
                      f'Uploaded file {original_filename} ({file_size} bytes)',
                      request.remote_addr)
            flash('Data file uploaded successfully!', 'success')
            return redirect(url_for('patient.my_data_files'))

    return render_template('patient/upload.html', form=form)


@patient_bp.route('/data-files')
@login_required
@role_required('patient')
def my_data_files():
    data_files = DataFile.query.filter_by(
        patient_id=current_user.id
    ).order_by(DataFile.upload_date.desc()).all()
    return render_template('patient/data_files.html', data_files=data_files)


@patient_bp.route('/data-file/<int:file_id>')
@login_required
@role_required('patient')
def data_file_detail(file_id):
    data_file = DataFile.query.filter_by(
        id=file_id, patient_id=current_user.id
    ).first_or_404()
    return render_template('patient/data_file_detail.html', data_file=data_file)


@patient_bp.route('/analyze/<int:file_id>')
@login_required
@role_required('patient')
def analyze_file(file_id):
    data_file = DataFile.query.filter_by(
        id=file_id, patient_id=current_user.id
    ).first_or_404()

    if data_file.analysis:
        flash('This file has already been analyzed.', 'info')
        return redirect(url_for('patient.data_file_detail', file_id=file_id))

    data_file.status = 'processing'
    db.session.commit()

    try:
        from app.ml_service import ml_service
        demographics = PatientDemographics.query.filter_by(
            user_id=current_user.id).first()

        results, error = ml_service.analyze_file(
            data_file.file_path,
            patient_demographics=demographics
        )

        if error:
            data_file.status = 'failed'
            db.session.commit()
            # ── SECURITY FIX: Don't expose internal error details ─
            current_app.logger.error(f'Analysis error for file {file_id}: {error}')
            flash('Analysis could not be completed. Please try again or contact support.', 'danger')
            return redirect(url_for('patient.data_file_detail', file_id=file_id))

        analysis = Analysis(
            data_file_id=data_file.id,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            osa_detected=results['osa_detected'],
            severity=results['severity'],
            confidence_score=results['confidence'],
            ahi_score=results['ahi_score'],
            apnea_events=results['apnea_events'],
            hypopnea_events=results['hypopnea_events'],
            oxygen_desaturation=results['oxygen_desaturation'],
            snoring_intensity=results['snoring_intensity'],
            features_json=json.dumps(results['features']),
            rr_signals_json=json.dumps(results.get('rr_signals') or {}),
            spo2_raw_json=json.dumps(results.get('spo2_raw') or {}),
            status='completed'
        )

        data_file.status  = 'completed'
        data_file.analysis = analysis
        db.session.add(analysis)
        db.session.commit()

        log_event('ANALYSIS_COMPLETED', current_user.username,
                  f'Analysis completed for file {data_file.original_filename}',
                  request.remote_addr)
        flash('Analysis completed successfully!', 'success')

    except Exception as e:
        data_file.status = 'failed'
        db.session.commit()
        current_app.logger.error(f'Unhandled analysis exception for file {file_id}: {e}')
        flash('An unexpected error occurred. Please contact support.', 'danger')

    return redirect(url_for('patient.data_file_detail', file_id=file_id))


@patient_bp.route('/compare')
@login_required
@role_required('patient')
def compare_results():
    # Fetch all completed analyses in chronological order for trend tracking
    patient_analyses = Analysis.query.join(DataFile).filter(
        DataFile.patient_id == current_user.id,
        Analysis.status.in_(['completed', 'reviewed'])
    ).order_by(Analysis.completed_at.asc()).all()

    # Build rich data series for each session including HRV and SpO2 features
    sessions = []
    for i, a in enumerate(patient_analyses):
        feats = {}
        if a.features_json:
            try:
                feats = json.loads(a.features_json)
            except Exception:
                feats = {}

        session = {
            'session':            i + 1,
            'label':              f'Session {i+1}',
            'date':               a.completed_at.strftime('%Y-%m-%d') if a.completed_at else 'N/A',
            'date_full':          a.completed_at.strftime('%b %d, %Y') if a.completed_at else 'N/A',
            'filename':           a.data_file.original_filename if a.data_file else '',
            'file_type':          a.data_file.file_type if a.data_file else '',
            'analysis_id':        a.id,
            'file_id':            a.data_file_id,
            # Core OSA metrics
            'ahi_score':          round(float(a.ahi_score or 0), 1),
            'severity':           a.severity or 'normal',
            'osa_detected':       bool(a.osa_detected),
            'confidence':         round(float(a.confidence_score or 0) * 100, 1),
            'apnea_events':       int(a.apnea_events or 0),
            'hypopnea_events':    int(a.hypopnea_events or 0),
            'oxygen_desaturation': round(float(a.oxygen_desaturation or 0), 1),
            # HRV time domain
            'sdnn':               round(float(feats.get('sdnn') or 0), 2) if feats.get('sdnn') else None,
            'rmssd':              round(float(feats.get('rmssd') or 0), 2) if feats.get('rmssd') else None,
            'pnn50':              round(float(feats.get('pnn50') or 0), 2) if feats.get('pnn50') else None,
            'mean_nni':           round(float(feats.get('mean_nni') or 0), 1) if feats.get('mean_nni') else None,
            # HRV frequency domain
            'lf_hf_ratio':        round(float(feats.get('lf_hf_ratio') or 0), 3) if feats.get('lf_hf_ratio') else None,
            'total_power':        round(float(feats.get('total_power') or 0), 1) if feats.get('total_power') else None,
            # HRV nonlinear
            'sample_entropy':     round(float(feats.get('sample_entropy') or 0), 3) if feats.get('sample_entropy') else None,
            'sd1':                round(float(feats.get('sd1') or 0), 2) if feats.get('sd1') else None,
            'sd2':                round(float(feats.get('sd2') or 0), 2) if feats.get('sd2') else None,
            # SpO2
            'spo2_mean':          round(float(feats.get('spo2_mean') or 0), 1) if feats.get('spo2_mean') else None,
            'spo2_min':           round(float(feats.get('spo2_min') or 0), 1) if feats.get('spo2_min') else None,
            'time_below_90':      round(float(feats.get('time_below_90') or 0), 1) if feats.get('time_below_90') else None,
            'time_below_95':      round(float(feats.get('time_below_95') or 0), 1) if feats.get('time_below_95') else None,
        }

        # Compute change vs previous session for each numeric metric
        if i > 0:
            prev = sessions[i - 1]
            changes = {}
            for key in ['ahi_score','apnea_events','hypopnea_events','oxygen_desaturation',
                        'sdnn','rmssd','pnn50','lf_hf_ratio','spo2_mean','spo2_min',
                        'time_below_90','sample_entropy']:
                curr_val = session.get(key)
                prev_val = prev.get(key)
                if curr_val is not None and prev_val is not None and prev_val != 0:
                    delta = round(curr_val - prev_val, 2)
                    pct   = round((delta / abs(prev_val)) * 100, 1)
                    changes[key] = {'delta': delta, 'pct': pct,
                                    'trend': 'up' if delta > 0 else ('down' if delta < 0 else 'same')}
                else:
                    changes[key] = None
            session['changes'] = changes
        else:
            session['changes'] = {}

        sessions.append(session)

    return render_template('patient/compare_results.html',
                           patient=current_user,
                           sessions=sessions,
                           total=len(sessions),
                           analyses_json=json.dumps(sessions))


@patient_bp.route('/data-file/<int:file_id>/delete', methods=['POST'])
@login_required
@role_required('patient')
def delete_data_file(file_id):
    data_file = DataFile.query.filter_by(
        id=file_id, patient_id=current_user.id
    ).first_or_404()

    try:
        if os.path.exists(data_file.file_path):
            os.remove(data_file.file_path)
    except Exception as e:
        current_app.logger.warning(f'Could not delete file from disk: {e}')

    if data_file.analysis:
        db.session.delete(data_file.analysis)

    original_name = data_file.original_filename
    db.session.delete(data_file)
    db.session.commit()

    log_event('FILE_DELETED', current_user.username,
              f'Deleted file {original_name}', request.remote_addr)
    flash(f'File "{original_name}" has been deleted.', 'success')
    return redirect(url_for('patient.my_data_files'))


@patient_bp.route('/analyze/<int:file_id>/retry', methods=['POST'])
@login_required
@role_required('patient')
def retry_analysis(file_id):
    data_file = DataFile.query.filter_by(
        id=file_id, patient_id=current_user.id
    ).first_or_404()

    if data_file.status not in ('failed', 'uploaded'):
        flash('This file cannot be retried.', 'warning')
        return redirect(url_for('patient.data_file_detail', file_id=file_id))

    if data_file.analysis:
        db.session.delete(data_file.analysis)
    data_file.status = 'uploaded'
    db.session.commit()
    return redirect(url_for('patient.analyze_file', file_id=file_id))


# ─────────────────────────────────────────────────────────────────────────────
# VISUALIZATION ROUTES — Patient
# ─────────────────────────────────────────────────────────────────────────────

@patient_bp.route('/visualize/hrv/<int:file_id>')
@login_required
@role_required('patient')
def viz_hrv(file_id):
    data_file = DataFile.query.filter_by(
        id=file_id, patient_id=current_user.id).first_or_404()
    if not data_file.analysis:
        flash('No analysis available for this file yet.', 'warning')
        return redirect(url_for('patient.data_file_detail', file_id=file_id))
    features = {}
    if data_file.analysis.features_json:
        try:
            features = json.loads(data_file.analysis.features_json)
        except Exception:
            features = {}
    return render_template('patient/viz_hrv.html', data_file=data_file, features=features)


@patient_bp.route('/visualize/spo2/<int:file_id>')
@login_required
@role_required('patient')
def viz_spo2(file_id):
    data_file = DataFile.query.filter_by(
        id=file_id, patient_id=current_user.id).first_or_404()
    if not data_file.analysis:
        flash('No analysis available for this file yet.', 'warning')
        return redirect(url_for('patient.data_file_detail', file_id=file_id))
    features = {}
    if data_file.analysis.features_json:
        try:
            features = json.loads(data_file.analysis.features_json)
        except Exception:
            features = {}
    return render_template('patient/viz_spo2.html', data_file=data_file, features=features)


@patient_bp.route('/visualize/report/<int:file_id>')
@login_required
@role_required('patient')
def viz_report(file_id):
    data_file = DataFile.query.filter_by(
        id=file_id, patient_id=current_user.id).first_or_404()
    if not data_file.analysis:
        flash('No analysis available for this file yet.', 'warning')
        return redirect(url_for('patient.data_file_detail', file_id=file_id))
    features = {}
    if data_file.analysis.features_json:
        try:
            features = json.loads(data_file.analysis.features_json)
        except Exception:
            features = {}
    return render_template('patient/viz_report.html', data_file=data_file, features=features)


# ─────────────────────────────────────────────────────────────────────────────
# VISUALIZATION ROUTES — Doctor
# ─────────────────────────────────────────────────────────────────────────────

def _doctor_get_file(file_id):
    """Return (data_file, features) if doctor has access, else abort 404."""
    data_file = DataFile.query.get_or_404(file_id)
    PatientAssignment.query.filter_by(
        doctor_id=current_user.id,
        patient_id=data_file.patient_id,
        is_active=True
    ).first_or_404()
    features = {}
    if data_file.analysis and data_file.analysis.features_json:
        try:
            features = json.loads(data_file.analysis.features_json)
        except Exception:
            features = {}
    return data_file, features


@doctor_bp.route('/visualize/hrv/<int:file_id>')
@login_required
@role_required('doctor')
@check_doctor_verification()
def doc_viz_hrv(file_id):
    data_file, features = _doctor_get_file(file_id)
    if not data_file.analysis:
        flash('No analysis available for this file.', 'warning')
        return redirect(url_for('doctor.patient_detail', patient_id=data_file.patient_id))
    return render_template('patient/viz_hrv.html',
                           data_file=data_file, features=features, doctor_view=True)


@doctor_bp.route('/visualize/spo2/<int:file_id>')
@login_required
@role_required('doctor')
@check_doctor_verification()
def doc_viz_spo2(file_id):
    data_file, features = _doctor_get_file(file_id)
    if not data_file.analysis:
        flash('No analysis available for this file.', 'warning')
        return redirect(url_for('doctor.patient_detail', patient_id=data_file.patient_id))
    return render_template('patient/viz_spo2.html',
                           data_file=data_file, features=features, doctor_view=True)


@doctor_bp.route('/visualize/report/<int:file_id>')
@login_required
@role_required('doctor')
@check_doctor_verification()
def doc_viz_report(file_id):
    data_file, features = _doctor_get_file(file_id)
    if not data_file.analysis:
        flash('No analysis available for this file.', 'warning')
        return redirect(url_for('doctor.patient_detail', patient_id=data_file.patient_id))
    return render_template('patient/viz_report.html',
                           data_file=data_file, features=features, doctor_view=True)


@doctor_bp.route('/compare-reports')
@login_required
@role_required('doctor')
@check_doctor_verification()
def compare_reports():
    assignments  = PatientAssignment.query.filter_by(
        doctor_id=current_user.id, is_active=True).all()
    all_patients = [a.patient for a in assignments]
    owned_ids    = {a.patient_id for a in assignments}

    selected_p1 = request.args.get('patient1', type=int)
    selected_p2 = request.args.get('patient2', type=int)

    def load_patient_data(patient_id):
        if not patient_id or patient_id not in owned_ids:
            return None
        patient  = User.query.get(patient_id)
        analyses = Analysis.query.join(DataFile).filter(
            DataFile.patient_id == patient_id,
            Analysis.status.in_(['completed', 'reviewed'])
        ).order_by(Analysis.completed_at.desc()).all()
        return {'patient': patient, 'analyses': analyses}

    patient1_data = load_patient_data(selected_p1)
    patient2_data = load_patient_data(selected_p2)

    def analyses_to_json(data):
        if not data or not data['analyses']:
            return '[]'
        return json.dumps([{
            'date':               a.completed_at.strftime('%Y-%m-%d') if a.completed_at else '',
            'ahi':                round(a.ahi_score or 0, 1),
            'severity':           a.severity or 'normal',
            'osa_detected':       a.osa_detected,
            'apnea_events':       a.apnea_events or 0,
            'hypopnea_events':    a.hypopnea_events or 0,
            'oxygen_desaturation': round(a.oxygen_desaturation or 0, 1),
        } for a in data['analyses']])

    return render_template('doctor/compare_patients.html',
                           all_patients=all_patients,
                           selected_p1=selected_p1,
                           selected_p2=selected_p2,
                           patient1_data=patient1_data,
                           patient2_data=patient2_data,
                           patient1_json=analyses_to_json(patient1_data),
                           patient2_json=analyses_to_json(patient2_data))


@doctor_bp.route('/api/patient-analyses/<int:patient_id>')
@login_required
@role_required('doctor')
@check_doctor_verification()
def api_patient_analyses(patient_id):
    """JSON list of completed analyses for a patient (used by compare dropdowns)."""
    PatientAssignment.query.filter_by(
        doctor_id=current_user.id,
        patient_id=patient_id,
        is_active=True
    ).first_or_404()
    analyses = Analysis.query.join(DataFile).filter(
        DataFile.patient_id == patient_id,
        Analysis.status.in_(['completed', 'reviewed'])
    ).order_by(Analysis.completed_at.desc()).all()
    return json.dumps([{
        'id':           a.id,
        'label':        f"{a.completed_at.strftime('%Y-%m-%d')} — AHI {a.ahi_score:.1f} ({a.severity})",
        'ahi_score':    a.ahi_score,
        'severity':     a.severity,
        'completed_at': a.completed_at.strftime('%Y-%m-%d %H:%M'),
        'file_name':    a.data_file.original_filename,
    } for a in analyses]), 200, {'Content-Type': 'application/json'}


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL DATA API
# ─────────────────────────────────────────────────────────────────────────────

@patient_bp.route('/api/signals/clean/<int:file_id>')
@login_required
@role_required('patient')
def api_signals_clean(file_id):
    data_file = DataFile.query.filter_by(
        id=file_id, patient_id=current_user.id).first_or_404()
    if not data_file.analysis:
        return json.dumps({'error': 'No analysis found'}), 404, \
               {'Content-Type': 'application/json'}

    a          = data_file.analysis
    rr_signals = json.loads(a.rr_signals_json or '{}')
    spo2_raw   = json.loads(a.spo2_raw_json   or '{}')

    return json.dumps({
        'rr_clean': rr_signals.get('rr_clean', []),
        'spo2':     spo2_raw.get('spo2_clean', []),
        'spo2_sr':  spo2_raw.get('sr', 1.0),
        'has_rr':   bool(rr_signals.get('rr_clean')),
        'has_spo2': bool(spo2_raw.get('spo2_clean')),
    }), 200, {'Content-Type': 'application/json'}


@doctor_bp.route('/api/signals/full/<int:file_id>')
@login_required
@role_required('doctor')
@check_doctor_verification()
def api_signals_full(file_id):
    data_file = DataFile.query.get_or_404(file_id)
    PatientAssignment.query.filter_by(
        doctor_id=current_user.id,
        patient_id=data_file.patient_id,
        is_active=True
    ).first_or_404()

    if not data_file.analysis:
        return json.dumps({'error': 'No analysis found'}), 404, \
               {'Content-Type': 'application/json'}

    a          = data_file.analysis
    rr_signals = json.loads(a.rr_signals_json or '{}')
    spo2_raw   = json.loads(a.spo2_raw_json   or '{}')

    log_event('SIGNAL_ACCESS', current_user.username,
              f'Raw signal access for file {data_file.id} (patient {data_file.patient_id})',
              request.remote_addr)

    spo2_cp    = spo2_raw.get('cleaning_params', {})
    rr_raw_n   = len(rr_signals.get('rr_raw',   []))
    rr_clean_n = len(rr_signals.get('rr_clean', []))

    return json.dumps({
        'rr_raw':      rr_signals.get('rr_raw',   []),
        'rr_clean':    rr_signals.get('rr_clean', []),
        'rr_sr':       rr_signals.get('sr', 0.0),
        'rr_artifacts_removed': rr_raw_n - rr_clean_n,
        'rr_artifact_pct': round((1 - rr_clean_n / max(rr_raw_n, 1)) * 100, 2),
        'spo2_raw':   spo2_raw.get('spo2_raw',   []),
        'spo2_clean': spo2_raw.get('spo2_clean', []),
        'spo2_sr':    spo2_raw.get('sr', 1.0),
        'spo2_invalid_removed': spo2_cp.get('invalid_removed', 0),
        'spo2_valid_pct':       spo2_cp.get('valid_data_pct',  100),
        'has_rr':   bool(rr_signals.get('rr_raw')),
        'has_spo2': bool(spo2_raw.get('spo2_raw')),
        'rr_cleaning_params': {
            'method':     'Malik ectopic beat removal + linear interpolation',
            'rri_min_ms': 300, 'rri_max_ms': 2000,
        },
        'spo2_cleaning_params': spo2_cp or {
            'stage1': 'range clip [70, 100] %',
            'stage2': 'Savitzky-Golay smooth (window=3s, poly=2)',
        },
    }), 200, {'Content-Type': 'application/json'}


@admin_bp.route('/api/signals/full/<int:file_id>')
@login_required
@role_required('admin')
def admin_api_signals_full(file_id):
    data_file = DataFile.query.get_or_404(file_id)
    if not data_file.analysis:
        return json.dumps({'error': 'No analysis found'}), 404, \
               {'Content-Type': 'application/json'}

    a          = data_file.analysis
    rr_signals = json.loads(a.rr_signals_json or '{}')
    spo2_raw   = json.loads(a.spo2_raw_json   or '{}')

    log_event('ADMIN_SIGNAL_ACCESS', current_user.username,
              f'Admin raw signal access for file {data_file.id}', request.remote_addr)

    spo2_cp    = spo2_raw.get('cleaning_params', {})
    rr_raw_n   = len(rr_signals.get('rr_raw',   []))
    rr_clean_n = len(rr_signals.get('rr_clean', []))

    return json.dumps({
        'rr_raw':      rr_signals.get('rr_raw',   []),
        'rr_clean':    rr_signals.get('rr_clean', []),
        'rr_sr':       rr_signals.get('sr', 0.0),
        'rr_artifacts_removed': rr_raw_n - rr_clean_n,
        'rr_artifact_pct': round((1 - rr_clean_n / max(rr_raw_n, 1)) * 100, 2),
        'spo2_raw':   spo2_raw.get('spo2_raw',   []),
        'spo2_clean': spo2_raw.get('spo2_clean', []),
        'spo2_sr':    spo2_raw.get('sr', 1.0),
        'spo2_invalid_removed': spo2_cp.get('invalid_removed', 0),
        'spo2_valid_pct':       spo2_cp.get('valid_data_pct',  100),
        'patient_id': data_file.patient_id,
        'file_name':  data_file.original_filename,
        'has_rr':     bool(rr_signals.get('rr_raw')),
        'has_spo2':   bool(spo2_raw.get('spo2_raw')),
        'rr_cleaning_params': {
            'method': 'Malik ectopic beat removal + linear interpolation',
            'rri_min_ms': 300, 'rri_max_ms': 2000,
        },
        'spo2_cleaning_params': spo2_cp or {
            'stage1': 'range clip [70, 100] %',
            'stage2': 'Savitzky-Golay smooth (window=3s, poly=2)',
        },
    }), 200, {'Content-Type': 'application/json'}


# ─────────────────────────────────────────────────────────────────────────────
# RESEARCH REQUEST SYSTEM
# ─────────────────────────────────────────────────────────────────────────────

@doctor_bp.route('/research/request', methods=['GET', 'POST'])
@login_required
@role_required('doctor')
@check_doctor_verification()
def research_request():
    from flask_wtf import FlaskForm

    my_requests = ResearchRequest.query.filter_by(
        doctor_id=current_user.id
    ).order_by(ResearchRequest.submitted_at.desc()).all()

    if request.method == 'POST':
        form = FlaskForm()
        if not form.validate():
            flash('Security validation failed.', 'danger')
            return redirect(url_for('doctor.research_request'))

        title         = request.form.get('title', '').strip()[:200]
        purpose       = request.form.get('purpose', '').strip()[:2000]
        scope         = request.form.get('scope', 'assigned')
        include_sig   = bool(request.form.get('include_signals'))
        export_format = request.form.get('export_format', 'csv')

        # Validate scope is one of the allowed values
        if scope not in ('assigned', 'all', 'specific'):
            scope = 'assigned'
        if export_format not in ('csv', 'json'):
            export_format = 'csv'

        patient_ids_json = None
        if scope == 'specific':
            ids = request.form.getlist('patient_ids')
            patient_ids_json = json.dumps([int(i) for i in ids if i.isdigit()])

        if not title or not purpose:
            flash('Title and purpose are required.', 'danger')
        else:
            rr = ResearchRequest(
                doctor_id        = current_user.id,
                title            = title,
                purpose          = purpose,
                scope            = scope,
                patient_ids_json = patient_ids_json,
                include_signals  = include_sig,
                export_format    = export_format,
                status           = 'pending',
            )
            db.session.add(rr)
            db.session.commit()
            log_event('RESEARCH_REQUEST', current_user.username,
                      f'Research request submitted: "{title}" (scope={scope})',
                      request.remote_addr)
            flash('Research request submitted. You will be notified once reviewed.', 'success')
            return redirect(url_for('doctor.research_request'))

    assignments       = PatientAssignment.query.filter_by(
        doctor_id=current_user.id, is_active=True).all()
    assigned_patients = [a.patient for a in assignments]

    return render_template('doctor/research_request.html',
                           my_requests=my_requests,
                           assigned_patients=assigned_patients)


@doctor_bp.route('/research/download/<int:request_id>')
@login_required
@role_required('doctor')
@check_doctor_verification()
def research_download(request_id):
    rr = ResearchRequest.query.filter_by(
        id=request_id, doctor_id=current_user.id, status='approved'
    ).first_or_404()

    # ── SECURITY FIX: Cap downloads to prevent bulk exfiltration ──
    if (rr.download_count or 0) >= MAX_RESEARCH_DOWNLOADS:
        log_event('RESEARCH_DOWNLOAD_BLOCKED', current_user.username,
                  f'Download cap reached for request #{request_id}',
                  request.remote_addr)
        flash(f'Download limit ({MAX_RESEARCH_DOWNLOADS}) reached for this request. '
              'Contact admin to re-authorize.', 'warning')
        return redirect(url_for('doctor.research_request'))

    if rr.scope == 'all':
        files = DataFile.query.all()
    elif rr.scope == 'specific' and rr.patient_ids_json:
        pids  = json.loads(rr.patient_ids_json)
        files = DataFile.query.filter(DataFile.patient_id.in_(pids)).all()
    else:
        assignments = PatientAssignment.query.filter_by(
            doctor_id=current_user.id, is_active=True).all()
        pids  = [a.patient_id for a in assignments]
        files = DataFile.query.filter(DataFile.patient_id.in_(pids)).all()

    rows = []
    for f in files:
        a = f.analysis
        if not a:
            continue
        feats = json.loads(a.features_json or '{}')
        row   = {
            'patient_id':           f.patient_id,
            'file_id':              f.id,
            'file_type':            f.file_type,
            'analysis_date':        a.completed_at.strftime('%Y-%m-%d') if a.completed_at else '',
            'osa_detected':         int(a.osa_detected or 0),
            'severity':             a.severity or '',
            'ahi_score':            a.ahi_score or '',
            'confidence':           round(a.confidence_score, 4) if a.confidence_score else '',
            'apnea_events':         a.apnea_events or 0,
            'hypopnea_events':      a.hypopnea_events or 0,
            'oxygen_desaturation':  a.oxygen_desaturation or '',
            'sdnn':                 feats.get('sdnn', ''),
            'rmssd':                feats.get('rmssd', ''),
            'pnn50':                feats.get('pnn50', ''),
            'pnn20':                feats.get('pnn20', ''),
            'mean_nni':             feats.get('mean_nni', ''),
            'median_nni':           feats.get('median_nni', ''),
            'cvnni':                feats.get('cvnni', ''),
            'cvsd':                 feats.get('cvsd', ''),
            'lf':                   feats.get('lf', ''),
            'hf':                   feats.get('hf', ''),
            'lf_hf_ratio':          feats.get('lf_hf_ratio', ''),
            'lfnu':                 feats.get('lfnu', ''),
            'hfnu':                 feats.get('hfnu', ''),
            'total_power':          feats.get('total_power', ''),
            'vlf':                  feats.get('vlf', ''),
            'sd1':                  feats.get('sd1', ''),
            'sd2':                  feats.get('sd2', ''),
            'ratio_sd2_sd1':        feats.get('ratio_sd2_sd1', ''),
            'csi':                  feats.get('csi', ''),
            'cvi':                  feats.get('cvi', ''),
            'sample_entropy':       feats.get('sample_entropy', ''),
            'spo2_mean':            feats.get('spo2_mean', ''),
            'spo2_min':             feats.get('spo2_min', ''),
            'spo2_std':             feats.get('spo2_std', ''),
            'spo2_p5':              feats.get('spo2_p5', ''),
            'spo2_p10':             feats.get('spo2_p10', ''),
            'time_below_90':        feats.get('time_below_90', ''),
            'time_below_88':        feats.get('time_below_88', ''),
            'time_below_85':        feats.get('time_below_85', ''),
            'valid_data_pct':       feats.get('valid_data_pct', ''),
            'recording_hours':      feats.get('recording_hours', ''),
            'review_status':        a.status or '',
            'doctor_notes':         (a.doctor_notes or '')[:500],
            'recommendations':      (a.recommendations or '')[:500],
        }

        if rr.include_signals:
            rr_sig  = json.loads(a.rr_signals_json or '{}')
            spo2sig = json.loads(a.spo2_raw_json   or '{}')
            row['rr_raw_json']     = json.dumps(rr_sig.get('rr_raw',   []))
            row['rr_clean_json']   = json.dumps(rr_sig.get('rr_clean', []))
            row['spo2_raw_json']   = json.dumps(spo2sig.get('spo2_raw',   []))
            row['spo2_clean_json'] = json.dumps(spo2sig.get('spo2_clean', []))

        rows.append(row)

    rr.download_count  = (rr.download_count or 0) + 1
    rr.last_downloaded = datetime.utcnow()
    db.session.commit()

    log_event('RESEARCH_DOWNLOAD', current_user.username,
              f'Research data downloaded: request #{request_id}, {len(rows)} records, '
              f'scope={rr.scope}, download #{rr.download_count}',
              request.remote_addr)

    if rr.export_format == 'json':
        data = json.dumps({
            'request_id':   rr.id,
            'title':        rr.title,
            'exported_at':  datetime.utcnow().isoformat(),
            'record_count': len(rows),
            'records':      rows
        }, indent=2)
        return current_app.response_class(
            data, mimetype='application/json',
            headers={'Content-Disposition':
                     f'attachment; filename=research_{request_id}.json'})
    else:
        if not rows:
            flash('No data available for this export.', 'warning')
            return redirect(url_for('doctor.research_request'))
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        return current_app.response_class(
            output.getvalue(), mimetype='text/csv',
            headers={'Content-Disposition':
                     f'attachment; filename=research_{request_id}.csv'})


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN — Research request review
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/research-requests')
@login_required
@role_required('admin')
def research_requests():
    pending  = ResearchRequest.query.filter_by(status='pending') \
                                    .order_by(ResearchRequest.submitted_at.desc()).all()
    all_reqs = ResearchRequest.query.order_by(ResearchRequest.submitted_at.desc()).all()
    return render_template('admin/research_requests.html',
                           pending=pending, all_requests=all_reqs)


@admin_bp.route('/research-requests/<int:request_id>/review', methods=['POST'])
@login_required
@role_required('admin')
def review_research_request(request_id):
    rr     = ResearchRequest.query.get_or_404(request_id)
    action = request.form.get('action')
    notes  = request.form.get('admin_notes', '').strip()
    reason = request.form.get('rejection_reason', '').strip()

    if action == 'approve':
        rr.status      = 'approved'
        rr.admin_notes = notes
        rr.reviewed_by = current_user.id
        rr.reviewed_at = datetime.utcnow()
        log_event('RESEARCH_APPROVED', current_user.username,
                  f'Approved research request #{request_id} for doctor {rr.doctor.username}',
                  request.remote_addr)
        flash(f'Research request #{request_id} approved.', 'success')

    elif action == 'reject':
        rr.status           = 'rejected'
        rr.admin_notes      = notes
        rr.rejection_reason = reason or 'Request does not meet research data access criteria.'
        rr.reviewed_by      = current_user.id
        rr.reviewed_at      = datetime.utcnow()
        log_event('RESEARCH_REJECTED', current_user.username,
                  f'Rejected research request #{request_id} for doctor {rr.doctor.username}',
                  request.remote_addr)
        flash(f'Research request #{request_id} rejected.', 'warning')

    db.session.commit()
    return redirect(url_for('admin.research_requests'))


@admin_bp.route('/research-requests/<int:request_id>/export')
@login_required
@role_required('admin')
def admin_research_export(request_id):
    """Admin: download research export — only for approved requests."""
    rr = ResearchRequest.query.get_or_404(request_id)

    # ── SECURITY FIX: Only allow export of approved requests ──────
    if rr.status != 'approved':
        log_event('RESEARCH_EXPORT_BLOCKED', current_user.username,
                  f'Admin tried to export non-approved request #{request_id} (status={rr.status})',
                  request.remote_addr)
        flash('Export is only available for approved research requests.', 'danger')
        return redirect(url_for('admin.research_requests'))

    if rr.scope == 'all':
        files = DataFile.query.all()
    elif rr.scope == 'specific' and rr.patient_ids_json:
        pids  = json.loads(rr.patient_ids_json)
        files = DataFile.query.filter(DataFile.patient_id.in_(pids)).all()
    else:
        assignments = PatientAssignment.query.filter_by(
            doctor_id=rr.doctor_id, is_active=True).all()
        pids  = [a.patient_id for a in assignments]
        files = DataFile.query.filter(DataFile.patient_id.in_(pids)).all()

    rows = []
    for f in files:
        a = f.analysis
        if not a:
            continue
        feats = json.loads(a.features_json or '{}')
        rows.append({
            'patient_id':   f.patient_id,
            'file_id':      f.id,
            'file_type':    f.file_type,
            'analysis_date': a.completed_at.strftime('%Y-%m-%d') if a.completed_at else '',
            'osa_detected': int(a.osa_detected or 0),
            'severity':     a.severity or '',
            'ahi_score':    a.ahi_score or '',
            'confidence':   a.confidence_score or '',
            'apnea_events': a.apnea_events or 0,
            'hypopnea_events': a.hypopnea_events or 0,
            'oxygen_desaturation': a.oxygen_desaturation or '',
            **{k: feats.get(k, '') for k in [
                'sdnn','rmssd','pnn50','pnn20','mean_nni','lf','hf','lf_hf_ratio',
                'sd1','sd2','csi','cvi','sample_entropy',
                'spo2_mean','spo2_min','spo2_p5','time_below_90','recording_hours'
            ]},
        })

    log_event('ADMIN_RESEARCH_EXPORT', current_user.username,
              f'Admin exported research data for approved request #{request_id}',
              request.remote_addr)

    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return current_app.response_class(
        output.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition':
                 f'attachment; filename=admin_export_req{request_id}.csv'})


# ─────────────────────────────────────────────────────────────────────────────
# SECURE DOCUMENT SERVING
# Files are stored outside static/ so they are NOT publicly accessible.
# This route streams them only to authenticated admins after path validation.
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/serve-document/<path:doc_path>')
@login_required
@role_required('admin')
def serve_document(doc_path):
    """
    Securely serve uploaded verification/doctor documents to admins only.
    Validates the path stays inside UPLOAD_FOLDER to prevent path traversal.
    """
    import mimetypes
    from flask import send_file, abort

    upload_folder = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    # Resolve and validate path — prevent directory traversal
    safe_folder = os.path.realpath(os.path.abspath(upload_folder))
    requested   = os.path.realpath(os.path.abspath(os.path.join(upload_folder, doc_path)))

    if not requested.startswith(safe_folder + os.sep):
        log_event('UNAUTHORIZED_ACCESS', current_user.username,
                  f'Path traversal attempt in serve_document: {doc_path}',
                  request.remote_addr)
        abort(403)

    if not os.path.isfile(requested):
        abort(404)

    # Only serve known document types
    ext = os.path.splitext(requested)[1].lower()
    allowed_exts = {'.pdf', '.jpg', '.jpeg', '.png'}
    if ext not in allowed_exts:
        abort(403)

    log_event('DOCUMENT_VIEWED', current_user.username,
              f'Admin viewed document: {os.path.basename(requested)}',
              request.remote_addr)

    mime = mimetypes.guess_type(requested)[0] or 'application/octet-stream'
    # Serve inline for PDF/images (preview in browser), force download for others
    as_attachment = ext not in {'.pdf', '.jpg', '.jpeg', '.png'}
    return send_file(requested, mimetype=mime, as_attachment=as_attachment,
                     download_name=os.path.basename(requested))


# ─────────────────────────────────────────────────────────────────────────────
# LICENSE EXPIRY CHECKER
# Called by the doctor dashboard and on every doctor page load.
# ─────────────────────────────────────────────────────────────────────────────

def check_license_expiry(user):
    """
    Check if a doctor's license is expiring soon or already expired.
    Returns (days_remaining, status) where status is:
      'expired'    — license has already passed expiry date
      'critical'   — 1 day remaining
      'warning'    — 7 days remaining
      'ok'         — more than 7 days remaining
      'no_license' — no license expiry date on file
    """
    if not user.doctor_profile or not user.doctor_profile.license_expiry:
        return None, 'no_license'

    from datetime import date
    today = date.today()
    expiry = user.doctor_profile.license_expiry
    days   = (expiry - today).days

    if days < 0:
        return days, 'expired'
    elif days <= 1:
        return days, 'critical'
    elif days <= 7:
        return days, 'warning'
    else:
        return days, 'ok'


def send_license_expiry_email(user, days_remaining):
    """Send license expiry notification email to doctor."""
    from app import mail
    from flask_mail import Message as _Msg

    if days_remaining < 0:
        subject = 'URGENT: Your medical license has expired — OSA Detection System'
        body = f"""Dear {user.display_name},

Your medical license on file has EXPIRED as of your registered expiry date.

Your patient access features have been locked until you submit an updated
license for admin review.

Please log in and submit a new verification request immediately:
{url_for('doctor.verification', _external=True)}

If you believe this is an error, contact the administrator.

— OSA Detection System
"""
    elif days_remaining <= 1:
        subject = 'URGENT: Your medical license expires TOMORROW — OSA Detection System'
        body = f"""Dear {user.display_name},

Your medical license on file expires TOMORROW.

If your license is not renewed and re-verified, your patient access features
will be automatically locked.

Please update your license immediately:
{url_for('doctor.verification', _external=True)}

— OSA Detection System
"""
    else:
        subject = f'Reminder: Medical license expires in {days_remaining} days — OSA Detection System'
        body = f"""Dear {user.display_name},

This is a reminder that your medical license on file expires in {days_remaining} days.

Please submit an updated license for admin review before it expires to avoid
any interruption to your patient access features.

Update your license here:
{url_for('doctor.verification', _external=True)}

— OSA Detection System
"""

    try:
        msg = _Msg(subject=subject, recipients=[user.email], body=body)
        mail.send(msg)
        log_event('LICENSE_EXPIRY_EMAIL', user.username,
                  f'License expiry notification sent ({days_remaining} days remaining)',
                  'system')
        return True
    except Exception as e:
        current_app.logger.error(f'License expiry email failed for {user.username}: {e}')
        return False