# app/forms.py
# Security fixes applied:
#   - Password minimum raised to 12 chars (HIPAA / NIST 800-63B)
#   - Password complexity validator added (uppercase + digit + special char)
#   - Username restricted to safe characters (no XSS via username)
#   - Max length added to all TextAreaFields (DoS / DB bloat prevention)
#   - All existing NumberRange clinical guards preserved

import re
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import (StringField, PasswordField, TextAreaField, SelectField,
                     IntegerField, FloatField, BooleanField, DateField, SubmitField)
from wtforms.validators import (DataRequired, Email, EqualTo, ValidationError,
                                Optional, Length, NumberRange, Regexp)
from app.models import User


# ─────────────────────────────────────────────────────────────────
# CUSTOM VALIDATORS
# ─────────────────────────────────────────────────────────────────

def password_complexity(form, field):
    """
    Enforce clinical-grade password complexity.
    Requires: uppercase, lowercase, digit, and special character.
    Think of this like a 4-lock safe — all four must engage.
    """
    pw = field.data
    errors = []
    if not re.search(r'[A-Z]', pw):
        errors.append('one uppercase letter')
    if not re.search(r'[a-z]', pw):
        errors.append('one lowercase letter')
    if not re.search(r'\d', pw):
        errors.append('one digit')
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]', pw):
        errors.append('one special character (!@#$%^&* etc.)')
    if errors:
        raise ValidationError(
            f"Password must contain at least: {', '.join(errors)}."
        )


# ─────────────────────────────────────────────────────────────────
# AUTH FORMS
# ─────────────────────────────────────────────────────────────────

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[
        DataRequired(),
        Length(max=80)
    ])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')


class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[
        DataRequired(),
        Length(min=3, max=80),
        # SECURITY FIX: restrict to safe characters — prevents XSS via username
        Regexp(
            r'^[a-zA-Z0-9_.\-]+$',
            message='Username may only contain letters, numbers, underscores, hyphens, and dots.'
        )
    ])
    email = StringField('Email', validators=[
        DataRequired(),
        Email(),
        Length(max=120)
    ])
    # SECURITY FIX: minimum 12 chars + complexity (HIPAA / NIST 800-63B)
    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=12, message='Password must be at least 12 characters.'),
        password_complexity
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match.')
    ])
    role = SelectField(
        'Register as',
        choices=[('patient', 'Patient'), ('doctor', 'Doctor')],
        validators=[DataRequired()]
    )
    submit = SubmitField('Register')

    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('Username already exists.')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data.strip().lower()).first()
        if user:
            raise ValidationError('Email already registered.')


class ForgotPasswordForm(FlaskForm):
    email = StringField('Email', validators=[
        DataRequired(),
        Email(),
        Length(max=120)
    ])
    submit = SubmitField('Request Password Reset')


class ResetPasswordForm(FlaskForm):
    # SECURITY FIX: same 12-char + complexity requirement as registration
    password = PasswordField('New Password', validators=[
        DataRequired(),
        Length(min=12, message='Password must be at least 12 characters.'),
        password_complexity
    ])
    confirm_password = PasswordField('Confirm New Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match.')
    ])
    submit = SubmitField('Reset Password')


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[
        DataRequired(),
        Length(min=12, message='Password must be at least 12 characters.'),
        password_complexity
    ])
    confirm_password = PasswordField('Confirm New Password', validators=[
        DataRequired(),
        EqualTo('new_password', message='Passwords must match.')
    ])
    submit = SubmitField('Change Password')


class ChangeEmailForm(FlaskForm):
    new_email = StringField('New Email', validators=[
        DataRequired(),
        Email(),
        Length(max=120)
    ])
    confirm_email = StringField('Confirm New Email', validators=[
        DataRequired(),
        Email(),
        EqualTo('new_email', message='Email addresses must match.')
    ])
    password = PasswordField('Current Password', validators=[DataRequired()])
    submit = SubmitField('Change Email')


# ─────────────────────────────────────────────────────────────────
# PROFILE FORMS
# ─────────────────────────────────────────────────────────────────

class ProfileForm(FlaskForm):
    first_name = StringField('First Name', validators=[
        DataRequired(), Length(max=100)
    ])
    last_name = StringField('Last Name', validators=[
        DataRequired(), Length(max=100)
    ])
    phone = StringField('Phone Number', validators=[
        Optional(), Length(max=20)
    ])
    date_of_birth = DateField('Date of Birth', validators=[Optional()], format='%Y-%m-%d')
    gender = SelectField(
        'Gender',
        choices=[('', 'Select...'), ('male', 'Male'), ('female', 'Female'), ('other', 'Other')],
        validators=[Optional()]
    )
    address = TextAreaField('Address', validators=[
        Optional(),
        # SECURITY FIX: max length on all TextAreaFields
        Length(max=500)
    ])
    # SECURITY FIX: max length — medical history is PHI, cap at 5000 chars
    medical_history = TextAreaField('Medical History', validators=[
        Optional(), Length(max=5000)
    ])
    submit = SubmitField('Save Profile')


# ─────────────────────────────────────────────────────────────────
# DATA UPLOAD FORM
# ─────────────────────────────────────────────────────────────────

class DataFileUploadForm(FlaskForm):
    data_file = FileField('Select File', validators=[
        DataRequired(),
        FileAllowed(['csv', 'edf', 'txt'], 'Only CSV, EDF, and TXT files allowed!')
    ])
    notes = TextAreaField('Notes (Optional)', validators=[
        Optional(), Length(max=1000)
    ])
    submit = SubmitField('Upload File')


# ─────────────────────────────────────────────────────────────────
# DOCTOR FORMS
# ─────────────────────────────────────────────────────────────────

class AssignPatientForm(FlaskForm):
    patient_id = SelectField('Select Patient', coerce=int, validators=[DataRequired()])
    notes = TextAreaField('Assignment Notes', validators=[
        Optional(), Length(max=1000)
    ])
    submit = SubmitField('Assign Patient')


class DoctorVerificationForm(FlaskForm):
    verification_document = FileField(
        'Upload Medical License or Professional ID',
        validators=[
            DataRequired(),
            FileAllowed(['pdf', 'jpg', 'jpeg', 'png'], 'Only PDF and image files allowed!')
        ]
    )
    medical_license_number = StringField('Medical License Number', validators=[
        DataRequired(), Length(max=100)
    ])
    license_state = StringField('License State/Country', validators=[
        DataRequired(), Length(max=50)
    ])
    license_expiry = DateField('License Expiry Date', validators=[DataRequired()],
                               format='%Y-%m-%d')
    document_number = StringField('Document Number', validators=[
        DataRequired(), Length(max=100)
    ])
    notes = TextAreaField('Additional Notes', validators=[
        Optional(), Length(max=1000)
    ])
    submit = SubmitField('Submit for Verification')


class DoctorProfileForm(FlaskForm):
    medical_license_number = StringField('Medical License Number', validators=[
        DataRequired(), Length(max=100)
    ])
    license_state = StringField('License State/Country', validators=[
        DataRequired(), Length(max=50)
    ])
    license_expiry = DateField('License Expiry Date', validators=[DataRequired()],
                               format='%Y-%m-%d')
    medical_school = StringField('Medical School', validators=[
        DataRequired(), Length(max=200)
    ])
    graduation_year = IntegerField('Graduation Year', validators=[
        DataRequired(), NumberRange(min=1950, max=2030)
    ])
    degree = SelectField(
        'Degree',
        choices=[('', 'Select...'), ('MD', 'MD'), ('DO', 'DO'),
                 ('MBBS', 'MBBS'), ('DDS', 'DDS'), ('Other', 'Other')],
        validators=[DataRequired()]
    )
    primary_specialization = SelectField(
        'Primary Specialization',
        choices=[
            ('', 'Select...'),
            ('Sleep Medicine', 'Sleep Medicine'),
            ('Pulmonology', 'Pulmonology'),
            ('Neurology', 'Neurology'),
            ('Internal Medicine', 'Internal Medicine'),
            ('Family Medicine', 'Family Medicine'),
            ('Other', 'Other')
        ],
        validators=[DataRequired()]
    )
    secondary_specialization = StringField('Secondary Specialization', validators=[
        Optional(), Length(max=100)
    ])
    board_certifications = TextAreaField('Board Certifications', validators=[
        Optional(), Length(max=2000)
    ])
    years_of_experience = IntegerField('Years of Experience', validators=[
        DataRequired(), NumberRange(min=0, max=60)
    ])
    current_hospital = StringField('Current Hospital/Clinic', validators=[
        DataRequired(), Length(max=200)
    ])
    current_position = StringField('Current Position/Title', validators=[
        DataRequired(), Length(max=100)
    ])
    fellowships = TextAreaField('Fellowships', validators=[
        Optional(), Length(max=2000)
    ])
    research_interests = TextAreaField('Research Interests', validators=[
        Optional(), Length(max=2000)
    ])
    publications = TextAreaField('Key Publications', validators=[
        Optional(), Length(max=3000)
    ])
    professional_email = StringField('Professional Email', validators=[
        DataRequired(), Email(), Length(max=120)
    ])
    office_phone = StringField('Office Phone', validators=[
        DataRequired(), Length(max=20)
    ])
    office_address = TextAreaField('Office Address', validators=[
        DataRequired(), Length(max=500)
    ])
    bio = TextAreaField('Professional Bio', validators=[
        Optional(), Length(max=1000)
    ])
    languages_spoken = StringField('Languages Spoken', validators=[
        Optional(), Length(max=200)
    ])
    cv_document = FileField('Upload CV/Resume', validators=[
        Optional(), FileAllowed(['pdf'], 'Only PDF files allowed!')
    ])
    license_document = FileField('Upload Medical License', validators=[
        Optional(), FileAllowed(['pdf', 'jpg', 'jpeg', 'png'], 'PDF and images only!')
    ])
    certification_document = FileField('Upload Board Certification', validators=[
        Optional(), FileAllowed(['pdf', 'jpg', 'jpeg', 'png'], 'PDF and images only!')
    ])
    is_accepting_patients = BooleanField('Currently Accepting New Patients')
    submit = SubmitField('Save Profile')


# ─────────────────────────────────────────────────────────────────
# PATIENT DEMOGRAPHICS FORM
# All clinical NumberRange validators preserved from original
# ─────────────────────────────────────────────────────────────────

class PatientDemographicsForm(FlaskForm):
    age_s1 = IntegerField('Age', validators=[DataRequired(), NumberRange(min=0, max=120)])
    gender = SelectField(
        'Gender',
        choices=[('', 'Select...'), ('male', 'Male'), ('female', 'Female'), ('other', 'Other')],
        validators=[DataRequired()]
    )
    race = SelectField(
        'Race',
        choices=[
            ('', 'Select...'), ('white', 'White'),
            ('black', 'Black or African American'), ('asian', 'Asian'),
            ('hispanic', 'Hispanic or Latino'),
            ('native_american', 'Native American'),
            ('pacific_islander', 'Pacific Islander'),
            ('other', 'Other'), ('prefer_not_to_say', 'Prefer not to say')
        ],
        validators=[Optional()]
    )
    ethnicity = StringField('Ethnicity', validators=[Optional(), Length(max=50)])
    bmi_s1    = FloatField('BMI', validators=[Optional(), NumberRange(min=10, max=100)])
    weight    = FloatField('Weight (kg)', validators=[Optional(), NumberRange(min=20, max=300)])
    height    = FloatField('Height (cm)', validators=[Optional(), NumberRange(min=100, max=250)])
    waist     = FloatField('Waist Circumference (cm)', validators=[Optional(), NumberRange(min=40, max=200)])
    hip       = FloatField('Hip Circumference (cm)', validators=[Optional(), NumberRange(min=50, max=200)])
    neck20    = FloatField('Neck Circumference (cm)', validators=[Optional(), NumberRange(min=20, max=60)])
    systbp    = IntegerField('Systolic BP (mmHg)', validators=[Optional(), NumberRange(min=70, max=250)])
    diasbp    = IntegerField('Diastolic BP (mmHg)', validators=[Optional(), NumberRange(min=40, max=150)])
    syst120   = IntegerField('Systolic BP Reading 1', validators=[Optional(), NumberRange(min=70, max=250)])
    dias120   = IntegerField('Diastolic BP Reading 1', validators=[Optional(), NumberRange(min=40, max=150)])
    syst220   = IntegerField('Systolic BP Reading 2', validators=[Optional(), NumberRange(min=70, max=250)])
    dias220   = IntegerField('Diastolic BP Reading 2', validators=[Optional(), NumberRange(min=40, max=150)])

    # Hypertension flags
    htnderv_s1 = BooleanField('Diagnosed Hypertension')
    srhype     = BooleanField('Self-Reported Hypertension')
    htnmed1    = BooleanField('Taking Hypertension Medication')
    beta1      = BooleanField('Taking Beta Blocker')
    ace1       = BooleanField('Taking ACE Inhibitor')
    diuret1    = BooleanField('Taking Diuretic')
    lipid1     = BooleanField('Taking Lipid Medication')

    # Lipid panel
    chol = FloatField('Total Cholesterol (mg/dL)', validators=[Optional(), NumberRange(min=100, max=500)])
    hdl  = FloatField('HDL Cholesterol (mg/dL)', validators=[Optional(), NumberRange(min=20, max=150)])
    trig = FloatField('Triglycerides (mg/dL)', validators=[Optional(), NumberRange(min=50, max=1000)])
    parrptdiab = BooleanField('Parent Reported Diabetes')

    # Pulmonary
    fev1     = FloatField('FEV1 (L)', validators=[Optional(), NumberRange(min=0.5, max=8)])
    fvc      = FloatField('FVC (L)', validators=[Optional(), NumberRange(min=0.5, max=10)])
    emphys15 = BooleanField('Emphysema')
    crbron15 = BooleanField('Chronic Bronchitis')
    copd15   = BooleanField('COPD')
    asthma15 = BooleanField('Asthma')

    # Smoking
    smokstat_s1 = SelectField(
        'Smoking Status',
        choices=[('', 'Select...'), ('never', 'Never Smoked'),
                 ('former', 'Former Smoker'), ('current', 'Current Smoker')],
        validators=[Optional()]
    )
    evsmok15 = BooleanField('Ever Smoked')
    smknow15 = BooleanField('Currently Smoking')
    cigday15 = IntegerField('Cigarettes per Day', validators=[Optional(), NumberRange(min=0, max=100)])

    # Alcohol & coffee
    alcoh   = FloatField('Alcohol (drinks/week)', validators=[Optional(), NumberRange(min=0, max=100)])
    wine15  = FloatField('Wine (drinks/week)', validators=[Optional(), NumberRange(min=0, max=50)])
    beer15  = FloatField('Beer (drinks/week)', validators=[Optional(), NumberRange(min=0, max=50)])
    shots15 = FloatField('Shots (drinks/week)', validators=[Optional(), NumberRange(min=0, max=50)])
    coffee15 = FloatField('Coffee (cups/day)', validators=[Optional(), NumberRange(min=0, max=20)])

    # Cardiovascular history
    prev_hx_mi     = BooleanField('Previous History of MI')
    prev_hx_stroke = BooleanField('Previous History of Stroke')
    mi15    = BooleanField('Myocardial Infarction')
    stroke15 = BooleanField('Stroke')
    hf15    = BooleanField('Heart Failure')
    cabg15  = BooleanField('CABG Surgery')
    ca15    = BooleanField('Cardiac Arrest')
    angina15 = BooleanField('Angina')

    # Sleep assessment
    ess_s1   = IntegerField('Epworth Sleepiness Scale Score', validators=[Optional(), NumberRange(min=0, max=24)])
    hvsnrd02 = BooleanField('Do You Snore?')
    hosnr02  = IntegerField('How Often Snore? (days/week)', validators=[Optional(), NumberRange(min=0, max=7)])
    loudsn02 = IntegerField('Loud Snoring (1-5)', validators=[Optional(), NumberRange(min=1, max=5)])
    yrssnr02 = IntegerField('Years of Snoring', validators=[Optional(), NumberRange(min=0, max=100)])
    stpbrt02 = BooleanField('Stopped Breathing During Sleep')
    hostbr02 = IntegerField('How Often Stopped Breathing (days/week)', validators=[Optional(), NumberRange(min=0, max=7)])
    sleepy02 = IntegerField('Daytime Sleepiness (1-5)', validators=[Optional(), NumberRange(min=1, max=5)])
    slpill15 = BooleanField('Taking Sleep Pills')
    napshr15 = FloatField('Nap Hours per Day', validators=[Optional(), NumberRange(min=0, max=12)])

    # Quality of life
    pcs_s1   = FloatField('Physical Component Score', validators=[Optional(), NumberRange(min=0, max=100)])
    mcs_s1   = FloatField('Mental Component Score', validators=[Optional(), NumberRange(min=0, max=100)])
    genhth25 = IntegerField('General Health Score (1-5)', validators=[Optional(), NumberRange(min=1, max=5)])

    submit = SubmitField('Save Demographics')


# ─────────────────────────────────────────────────────────────────
# ADMIN / REVIEW FORMS
# ─────────────────────────────────────────────────────────────────

class AnalysisReviewForm(FlaskForm):
    status = SelectField(
        'Status',
        choices=[('pending', 'Pending'), ('reviewed', 'Reviewed')],
        validators=[DataRequired()]
    )
    doctor_notes    = TextAreaField('Clinical Notes', validators=[Optional(), Length(max=5000)])
    recommendations = TextAreaField('Recommendations', validators=[Optional(), Length(max=5000)])
    submit = SubmitField('Save Review')


class VerificationReviewForm(FlaskForm):
    status = SelectField(
        'Verification Status',
        choices=[('approved', 'Approve'), ('rejected', 'Reject')],
        validators=[DataRequired()]
    )
    review_notes     = TextAreaField('Review Notes', validators=[Optional(), Length(max=2000)])
    rejection_reason = TextAreaField('Rejection Reason', validators=[Optional(), Length(max=2000)])
    submit = SubmitField('Submit Review')