# app/models.py
# Security fixes applied:
#   1. failed_login_attempts + locked_until fields added (brute-force lockout)
#   2. Password hashing upgraded to scrypt (stronger than default pbkdf2)
#   3. Reset token stored as sha256 hash (never raw token in DB)
#   4. AuditLog now has user_id FK (audit trail survives username changes)
#   5. is_email_confirmed explicit flag (replaces fragile heuristic in login)
#   6. datetime.utcnow replaced with datetime.now(timezone.utc) (deprecation fix)
#   7. PHI fields annotated clearly for future encryption layer

from app import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
import hashlib


def _now():
    """UTC-aware current time. Single source of truth for all timestamps."""
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80),  unique=True, nullable=False, index=True)
    email         = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role          = db.Column(db.String(20),  nullable=False, default='patient')
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=_now)
    last_login    = db.Column(db.DateTime)

    # ── SECURITY FIX 1: Brute-force lockout (persistent across restarts) ──
    failed_login_attempts = db.Column(db.Integer, default=0)
    locked_until          = db.Column(db.DateTime, nullable=True)

    # ── SECURITY FIX 5: Explicit email confirmation flag ─────────────────
    # Replaces the fragile heuristic in login that checked
    # reset_token is None AND verified_at is None AND last_login is None
    is_email_confirmed = db.Column(db.Boolean, default=False)

    # Doctor verification
    two_factor_enabled        = db.Column(db.Boolean, default=False)
    two_factor_verified       = db.Column(db.Boolean, default=False)
    verification_document_path = db.Column(db.String(255))
    verification_status       = db.Column(db.String(20), default='not_submitted')
    verified_at               = db.Column(db.DateTime)
    verified_by               = db.Column(db.Integer, db.ForeignKey('users.id'))

    # ── SECURITY FIX 3: Reset token stores sha256 hash, not raw token ────
    # Raw token is emailed; if DB is breached hashes are useless.
    reset_token         = db.Column(db.String(64),  nullable=True)  # sha256 hex = 64 chars
    reset_token_expires = db.Column(db.DateTime,    nullable=True)

    # Pending email change (confirmed via link)
    pending_email = db.Column(db.String(120), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────
    profile = db.relationship('UserProfile', backref='user',
                              uselist=False, cascade='all, delete-orphan')
    doctor_profile = db.relationship('DoctorProfile', backref='user',
                                     uselist=False, cascade='all, delete-orphan')
    patient_demographics = db.relationship('PatientDemographics', backref='user',
                                           uselist=False, cascade='all, delete-orphan')
    assigned_patients = db.relationship('PatientAssignment',
                                        foreign_keys='PatientAssignment.doctor_id',
                                        backref='doctor', lazy='select')
    assigned_doctors  = db.relationship('PatientAssignment',
                                        foreign_keys='PatientAssignment.patient_id',
                                        backref='patient', lazy='select')
    data_files = db.relationship('DataFile', backref='patient',
                                 lazy='select', cascade='all, delete-orphan')

    # ── Password methods ──────────────────────────────────────────────────

    def set_password(self, password):
        """
        SECURITY FIX 2: Use scrypt instead of default pbkdf2:sha256.
        scrypt is memory-hard — much more expensive to brute-force.
        Think of pbkdf2 as a padlock vs scrypt as a vault door.
        """
        self.password_hash = generate_password_hash(password, method='scrypt')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    # ── Reset token methods ───────────────────────────────────────────────

    def set_reset_token(self, raw_token: str):
        """Store hashed token only. Raw token must be emailed, never stored."""
        self.reset_token = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()

    def verify_reset_token(self, raw_token: str) -> bool:
        """Check a submitted raw token against stored hash."""
        if not self.reset_token:
            return False
        return self.reset_token == hashlib.sha256(raw_token.encode('utf-8')).hexdigest()

    # ── Role helpers ──────────────────────────────────────────────────────

    def has_role(self, role):
        return self.role == role

    @property
    def display_name(self):
        if self.profile and self.profile.first_name:
            last = self.profile.last_name or ''
            return f"{self.profile.first_name} {last}".strip()
        return self.username

    @property
    def is_doctor_verified(self):
        return self.role == 'doctor' and self.verification_status == 'approved'

    def __repr__(self):
        return f'<User {self.username}>'


# ─────────────────────────────────────────────────────────────────────────────

class UserProfile(db.Model):
    __tablename__ = 'user_profiles'

    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, unique=True)

    # PHI fields — annotated for future application-layer encryption
    first_name   = db.Column(db.String(100))
    last_name    = db.Column(db.String(100))
    phone        = db.Column(db.String(20))
    date_of_birth = db.Column(db.Date)
    gender       = db.Column(db.String(20))
    address      = db.Column(db.Text)            # PHI
    medical_history = db.Column(db.Text)         # PHI — sensitive

    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)

    def __repr__(self):
        return f'<UserProfile {self.user_id}>'


class DoctorProfile(db.Model):
    __tablename__ = 'doctor_profiles'

    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, unique=True)

    medical_license_number  = db.Column(db.String(100))
    license_state           = db.Column(db.String(50))
    license_expiry          = db.Column(db.Date)
    medical_school          = db.Column(db.String(200))
    graduation_year         = db.Column(db.Integer)
    degree                  = db.Column(db.String(100))
    primary_specialization  = db.Column(db.String(100))
    secondary_specialization = db.Column(db.String(100))
    board_certifications    = db.Column(db.Text)
    years_of_experience     = db.Column(db.Integer)
    current_hospital        = db.Column(db.String(200))
    current_position        = db.Column(db.String(100))
    fellowships             = db.Column(db.Text)
    research_interests      = db.Column(db.Text)
    publications            = db.Column(db.Text)
    professional_email      = db.Column(db.String(120))
    office_phone            = db.Column(db.String(20))
    office_address          = db.Column(db.Text)
    cv_document_path        = db.Column(db.String(255))
    license_document_path   = db.Column(db.String(255))
    certification_document_path = db.Column(db.String(255))
    bio                     = db.Column(db.Text)
    languages_spoken        = db.Column(db.String(200))
    is_accepting_patients   = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)

    def __repr__(self):
        return f'<DoctorProfile {self.user_id}>'


class PatientDemographics(db.Model):
    __tablename__ = 'patient_demographics'

    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, unique=True)

    # ── Clinical measurements — all PHI ──────────────────────────
    age_s1    = db.Column(db.Integer)
    gender    = db.Column(db.String(20))
    race      = db.Column(db.String(50))
    ethnicity = db.Column(db.String(50))
    bmi_s1    = db.Column(db.Float)
    weight    = db.Column(db.Float)
    height    = db.Column(db.Float)
    waist     = db.Column(db.Float)
    hip       = db.Column(db.Float)
    neck20    = db.Column(db.Float)

    # Blood pressure
    systbp  = db.Column(db.Integer)
    diasbp  = db.Column(db.Integer)
    syst120 = db.Column(db.Integer)
    dias120 = db.Column(db.Integer)
    syst220 = db.Column(db.Integer)
    dias220 = db.Column(db.Integer)

    # Hypertension
    htnderv_s1 = db.Column(db.Boolean)
    srhype     = db.Column(db.Boolean)
    htnmed1    = db.Column(db.Boolean)
    beta1      = db.Column(db.Boolean)
    ace1       = db.Column(db.Boolean)
    diuret1    = db.Column(db.Boolean)
    lipid1     = db.Column(db.Boolean)

    # Lipids
    chol       = db.Column(db.Float)
    hdl        = db.Column(db.Float)
    trig       = db.Column(db.Float)
    parrptdiab = db.Column(db.Boolean)

    # Pulmonary
    fev1     = db.Column(db.Float)
    fvc      = db.Column(db.Float)
    emphys15 = db.Column(db.Boolean)
    crbron15 = db.Column(db.Boolean)
    copd15   = db.Column(db.Boolean)
    asthma15 = db.Column(db.Boolean)

    # Smoking
    smokstat_s1 = db.Column(db.String(20))
    evsmok15    = db.Column(db.Boolean)
    smknow15    = db.Column(db.Boolean)
    cigday15    = db.Column(db.Integer)

    # Alcohol & coffee
    alcoh   = db.Column(db.Float)
    wine15  = db.Column(db.Float)
    beer15  = db.Column(db.Float)
    shots15 = db.Column(db.Float)
    coffee15 = db.Column(db.Float)

    # Cardiovascular history
    prev_hx_mi     = db.Column(db.Boolean)
    prev_hx_stroke = db.Column(db.Boolean)
    mi15    = db.Column(db.Boolean)
    stroke15 = db.Column(db.Boolean)
    hf15    = db.Column(db.Boolean)
    cabg15  = db.Column(db.Boolean)
    ca15    = db.Column(db.Boolean)
    angina15 = db.Column(db.Boolean)

    # Sleep assessment
    ess_s1   = db.Column(db.Integer)
    hvsnrd02 = db.Column(db.Boolean)
    hosnr02  = db.Column(db.Integer)
    loudsn02 = db.Column(db.Integer)
    yrssnr02 = db.Column(db.Integer)
    stpbrt02 = db.Column(db.Boolean)
    hostbr02 = db.Column(db.Integer)
    sleepy02 = db.Column(db.Integer)
    slpill15 = db.Column(db.Boolean)
    napshr15 = db.Column(db.Float)

    # Quality of life
    pcs_s1   = db.Column(db.Float)
    mcs_s1   = db.Column(db.Float)
    genhth25 = db.Column(db.Integer)

    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)

    def __repr__(self):
        return f'<PatientDemographics {self.user_id}>'


class PatientAssignment(db.Model):
    __tablename__ = 'patient_assignments'

    id         = db.Column(db.Integer, primary_key=True)
    doctor_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    assigned_at = db.Column(db.DateTime, default=_now)
    is_active  = db.Column(db.Boolean, default=True)
    notes      = db.Column(db.Text)

    __table_args__ = (
        db.UniqueConstraint('doctor_id', 'patient_id', name='unique_assignment'),
    )

    def __repr__(self):
        return f'<PatientAssignment Doctor:{self.doctor_id} Patient:{self.patient_id}>'


class DataFile(db.Model):
    __tablename__ = 'data_files'

    id                = db.Column(db.Integer, primary_key=True)
    patient_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    filename          = db.Column(db.String(255))
    file_path         = db.Column(db.String(500))
    file_type         = db.Column(db.String(20), nullable=False)
    file_size         = db.Column(db.Integer)
    upload_date       = db.Column(db.DateTime, default=_now)
    status            = db.Column(db.String(20), default='uploaded')

    analysis = db.relationship('Analysis', backref='data_file',
                               uselist=False, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<DataFile {self.original_filename}>'


class Analysis(db.Model):
    __tablename__ = 'analyses'

    id           = db.Column(db.Integer, primary_key=True)
    data_file_id = db.Column(db.Integer, db.ForeignKey('data_files.id'),
                             nullable=False, unique=True)

    osa_detected       = db.Column(db.Boolean)
    severity           = db.Column(db.String(20))
    ahi_score          = db.Column(db.Float)
    confidence_score   = db.Column(db.Float)
    total_sleep_time   = db.Column(db.Float)
    apnea_events       = db.Column(db.Integer)
    hypopnea_events    = db.Column(db.Integer)
    oxygen_desaturation = db.Column(db.Float)
    snoring_intensity  = db.Column(db.Float)
    started_at         = db.Column(db.DateTime)
    completed_at       = db.Column(db.DateTime)
    analysis_date      = db.Column(db.DateTime, default=_now)
    model_version      = db.Column(db.String(50))
    features_json      = db.Column(db.Text)
    rr_signals_json    = db.Column(db.Text, nullable=True)
    spo2_raw_json      = db.Column(db.Text, nullable=True)
    raw_results        = db.Column(db.Text)
    status             = db.Column(db.String(20), default='pending')
    reviewed_by        = db.Column(db.Integer, db.ForeignKey('users.id'))
    reviewed_at        = db.Column(db.DateTime)
    doctor_notes       = db.Column(db.Text)
    recommendations    = db.Column(db.Text)

    reviewer = db.relationship('User', foreign_keys=[reviewed_by])

    def __repr__(self):
        return f'<Analysis {self.id} OSA:{self.osa_detected}>'


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'

    id         = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(50),  nullable=False)
    username   = db.Column(db.String(80))

    # SECURITY FIX 4: user_id FK so audit trail survives username changes
    # nullable=True because we also log pre-login events (failed attempts)
    # where there may be no valid user_id yet
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    details    = db.Column(db.Text)
    ip_address = db.Column(db.String(45))          # IPv6-safe (max 45 chars)
    timestamp  = db.Column(db.DateTime, default=_now, index=True)

    def __repr__(self):
        return f'<AuditLog {self.event_type} by {self.username}>'


class DoctorVerificationRequest(db.Model):
    __tablename__ = 'doctor_verification_requests'

    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    document_type = db.Column(db.String(50))
    document_path = db.Column(db.String(255))
    document_number = db.Column(db.String(100))
    submitted_at  = db.Column(db.DateTime, default=_now)
    status        = db.Column(db.String(20), default='pending')
    reviewed_by   = db.Column(db.Integer, db.ForeignKey('users.id'))
    reviewed_at   = db.Column(db.DateTime)
    review_notes  = db.Column(db.Text)
    rejection_reason = db.Column(db.Text)

    user     = db.relationship('User', foreign_keys=[user_id],
                               backref='verification_requests')
    reviewer = db.relationship('User', foreign_keys=[reviewed_by])

    def __repr__(self):
        return f'<DoctorVerificationRequest {self.id} User:{self.user_id}>'


class ResearchRequest(db.Model):
    __tablename__ = 'research_requests'

    id               = db.Column(db.Integer, primary_key=True)
    doctor_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title            = db.Column(db.String(200), nullable=False)
    purpose          = db.Column(db.Text,    nullable=False)
    scope            = db.Column(db.String(20), default='assigned')
    patient_ids_json = db.Column(db.Text)
    include_signals  = db.Column(db.Boolean, default=False)
    export_format    = db.Column(db.String(10), default='csv')
    status           = db.Column(db.String(20), default='pending')
    submitted_at     = db.Column(db.DateTime, default=_now)
    reviewed_by      = db.Column(db.Integer, db.ForeignKey('users.id'))
    reviewed_at      = db.Column(db.DateTime)
    admin_notes      = db.Column(db.Text)
    rejection_reason = db.Column(db.Text)
    download_count   = db.Column(db.Integer, default=0)
    last_downloaded  = db.Column(db.DateTime)

    doctor   = db.relationship('User', foreign_keys=[doctor_id],
                               backref='research_requests')
    reviewer = db.relationship('User', foreign_keys=[reviewed_by])

    def __repr__(self):
        return f'<ResearchRequest {self.id} Doctor:{self.doctor_id} {self.status}>'