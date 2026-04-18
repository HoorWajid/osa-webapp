# create_admin.py
# Security fixes applied:
#   1. Password read from ADMIN_PASSWORD env var — never hardcoded
#   2. Email read from ADMIN_EMAIL env var — never hardcoded
#   3. Username read from ADMIN_USERNAME env var (default: admin)
#   4. Password validated against 12-char + complexity policy before saving
#   5. Script refuses to run if ANY admin role account already exists
#   6. is_email_confirmed set to True (admin bypasses email confirmation)
#   7. No credentials printed to console — only a success/failure message
#   8. Audit log entry created on admin creation

import os
import re
import sys

# ── Load .env before importing app ───────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()


def _validate_password(pw: str) -> list:
    """
    Return list of unmet requirements.
    Empty list means password is acceptable.
    Must match the complexity policy in forms.py password_complexity validator.
    Think of this as the same lock that the login form uses —
    the admin key must fit the same lock.
    """
    errors = []
    if len(pw) < 12:
        errors.append('at least 12 characters')
    if not re.search(r'[A-Z]', pw):
        errors.append('one uppercase letter')
    if not re.search(r'[a-z]', pw):
        errors.append('one lowercase letter')
    if not re.search(r'\d', pw):
        errors.append('one digit')
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]', pw):
        errors.append('one special character (!@#$%^&* etc.)')
    return errors


def main():
    # ── Read credentials from environment ────────────────────────────────
    username = os.environ.get('ADMIN_USERNAME', 'admin').strip()
    email    = os.environ.get('ADMIN_EMAIL',    '').strip()
    password = os.environ.get('ADMIN_PASSWORD', '').strip()

    # ── Validate all required fields are present ──────────────────────────
    missing = []
    if not email:
        missing.append('ADMIN_EMAIL')
    if not password:
        missing.append('ADMIN_PASSWORD')
    if missing:
        print(f"\n[ERROR] Missing required environment variables: {', '.join(missing)}")
        print("\nSet them in your .env file or environment before running:")
        print("  ADMIN_EMAIL=your@email.com")
        print("  ADMIN_PASSWORD=YourStr0ng!Pass")
        print("  ADMIN_USERNAME=admin  (optional, defaults to 'admin')\n")
        sys.exit(1)

    # ── Validate password complexity before touching the DB ───────────────
    errors = _validate_password(password)
    if errors:
        print(f"\n[ERROR] ADMIN_PASSWORD does not meet security requirements.")
        print(f"        Missing: {', '.join(errors)}")
        print(f"\n        Generate a strong password with:")
        print(f"        python -c \"import secrets,string; "
              f"print(secrets.token_urlsafe(16) + '!A1')\"")
        sys.exit(1)

    # ── Import app only after env validation passes ───────────────────────
    from app import create_app, db
    from app.models import User, UserProfile, AuditLog

    application = create_app()

    with application.app_context():

        # ── SECURITY FIX 5: Block if ANY admin account already exists ─────
        # Not just checking username='admin' — checking the role itself.
        # Like checking if the building already has a security chief
        # before appointing another one, regardless of their name.
        existing_admin = User.query.filter_by(role='admin').first()
        if existing_admin:
            print(f"\n[INFO] An admin account already exists "
                  f"(username: {existing_admin.username}).")
            print(      "       Delete it manually if you need to recreate it.\n")
            sys.exit(0)

        # ── Check username and email uniqueness ───────────────────────────
        if User.query.filter_by(username=username).first():
            print(f"\n[ERROR] Username '{username}' is already taken. "
                  f"Set ADMIN_USERNAME to a different value.\n")
            sys.exit(1)

        if User.query.filter_by(email=email).first():
            print(f"\n[ERROR] Email is already registered to another account.\n")
            sys.exit(1)

        # ── Create admin user ─────────────────────────────────────────────
        admin = User(
            username=username,
            email=email.lower(),
            role='admin',
            is_active=True,
            is_email_confirmed=True,       # FIX 6: bypass email confirmation
            verification_status='approved',
            failed_login_attempts=0,
        )
        admin.set_password(password)       # uses scrypt from our fixed models.py

        profile      = UserProfile()
        admin.profile = profile
        db.session.add(admin)

        # ── Audit log entry ───────────────────────────────────────────────
        log = AuditLog(
            event_type='ADMIN_CREATED',
            username=username,
            details=f'Admin account created via create_admin.py (email: {email})',
            ip_address='localhost'
        )
        db.session.add(log)
        db.session.commit()

        # ── FIX 7: Never print credentials — success message only ─────────
        print(f"\n[OK] Admin account created successfully.")
        print(f"     Username : {username}")
        print(f"     Login at : http://localhost:5000/login\n")


if __name__ == '__main__':
    main()