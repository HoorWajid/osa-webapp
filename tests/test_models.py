"""
Tests for app/models.py

Covers User model methods: password hashing, reset-token handling,
role helpers, display_name, and is_doctor_verified.
"""
import hashlib
import pytest

import os
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


class TestUserPassword:
    def test_set_password_hashes_value(self, patient_user):
        assert patient_user.password_hash != "TestPass123!"
        assert patient_user.password_hash is not None

    def test_check_password_correct(self, patient_user):
        assert patient_user.check_password("TestPass123!") is True

    def test_check_password_wrong(self, patient_user):
        assert patient_user.check_password("WrongPass") is False

    def test_different_passwords_produce_different_hashes(self, app, db):
        from app.models import User
        with app.app_context():
            u1 = User(username="u1x", email="u1x@t.lo", role="patient",
                      is_active=True, is_email_confirmed=True)
            u2 = User(username="u2x", email="u2x@t.lo", role="patient",
                      is_active=True, is_email_confirmed=True)
            u1.set_password("PasswordA1!")
            u2.set_password("PasswordB2!")
            assert u1.password_hash != u2.password_hash


class TestUserResetToken:
    def test_set_reset_token_stores_hash_not_raw(self, patient_user):
        raw = "supersecrettoken123"
        patient_user.set_reset_token(raw)
        assert patient_user.reset_token != raw
        expected_hash = hashlib.sha256(raw.encode()).hexdigest()
        assert patient_user.reset_token == expected_hash

    def test_verify_reset_token_correct(self, patient_user):
        raw = "supersecrettoken123"
        patient_user.set_reset_token(raw)
        assert patient_user.verify_reset_token(raw) is True

    def test_verify_reset_token_wrong(self, patient_user):
        patient_user.set_reset_token("correcttoken")
        assert patient_user.verify_reset_token("wrongtoken") is False

    def test_verify_reset_token_no_token_set(self, app, db):
        from app.models import User
        with app.app_context():
            u = User(username="notokenuser", email="nt@t.lo", role="patient",
                     is_active=True, is_email_confirmed=True)
            u.set_password("Pass1234!")
            assert u.verify_reset_token("anything") is False


class TestUserRoles:
    def test_has_role_patient(self, patient_user):
        assert patient_user.has_role("patient") is True
        assert patient_user.has_role("doctor") is False

    def test_has_role_doctor(self, doctor_user):
        assert doctor_user.has_role("doctor") is True
        assert doctor_user.has_role("patient") is False

    def test_is_doctor_verified_true(self, doctor_user):
        assert doctor_user.is_doctor_verified is True

    def test_is_doctor_verified_false_for_patient(self, patient_user):
        assert patient_user.is_doctor_verified is False

    def test_is_doctor_verified_false_when_pending(self, app, db):
        from app.models import User
        with app.app_context():
            u = User(username="pendingdoc", email="pd@t.lo", role="doctor",
                     is_active=True, is_email_confirmed=True,
                     verification_status="pending")
            u.set_password("Pass1234!")
            assert u.is_doctor_verified is False


class TestUserDisplayName:
    def test_display_name_falls_back_to_username(self, patient_user):
        assert patient_user.display_name == "testpatient"

    def test_display_name_uses_profile_when_set(self, app, db, patient_user):
        from app.models import UserProfile
        with app.app_context():
            profile = UserProfile(
                user_id=patient_user.id,
                first_name="Alice",
                last_name="Smith",
            )
            db.session.add(profile)
            db.session.commit()
            # Re-query to get relationship loaded
            from app.models import User
            user = User.query.get(patient_user.id)
            assert user.display_name == "Alice Smith"

    def test_display_name_first_name_only(self, app, db, patient_user):
        from app.models import UserProfile
        with app.app_context():
            # Remove any existing profile first
            existing = UserProfile.query.filter_by(user_id=patient_user.id).first()
            if existing:
                db.session.delete(existing)
                db.session.commit()
            profile = UserProfile(user_id=patient_user.id, first_name="Bob")
            db.session.add(profile)
            db.session.commit()
            from app.models import User
            user = User.query.get(patient_user.id)
            assert user.display_name == "Bob"


class TestUserRepr:
    def test_repr(self, patient_user):
        assert "testpatient" in repr(patient_user)
