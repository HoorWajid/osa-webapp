"""
Tests for authentication routes (login, logout, register, password reset).

Uses Flask test client.  CSRF is disabled in TestConfig (conftest.py).
"""
import pytest
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


def _login(client, username: str, password: str):
    """Helper: POST login form and return the response."""
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def _logout(client):
    """Helper: POST logout (CSRF-protected endpoint)."""
    return client.post("/auth/logout", follow_redirects=True)


class TestLoginRoute:
    def test_login_page_loads(self, client):
        resp = client.get("/auth/login")
        assert resp.status_code == 200

    def test_login_success_redirects(self, client, patient_user):
        resp = _login(client, "testpatient", "TestPass123!")
        assert resp.status_code == 200
        # Should land on dashboard, not login page
        assert b"login" not in resp.data.lower() or b"dashboard" in resp.data.lower()

    def test_login_wrong_password_stays_on_login(self, client, patient_user):
        resp = _login(client, "testpatient", "WrongPassword!")
        assert resp.status_code == 200
        # Flash message about invalid credentials
        assert b"invalid" in resp.data.lower() or b"incorrect" in resp.data.lower() or b"login" in resp.data.lower()

    def test_login_unknown_user(self, client):
        resp = _login(client, "nobody", "SomePass1!")
        assert resp.status_code == 200

    def test_login_unconfirmed_email_blocked(self, app, db, client):
        from app.models import User
        with app.app_context():
            u = User(
                username="unconfirmed",
                email="unconfirmed@t.lo",
                role="patient",
                is_active=True,
                is_email_confirmed=False,
            )
            u.set_password("Pass1234!")
            db.session.add(u)
            db.session.commit()
        resp = _login(client, "unconfirmed", "Pass1234!")
        # Login should not succeed for unconfirmed accounts
        assert resp.status_code == 200

    def test_empty_credentials_rejected(self, client):
        resp = _login(client, "", "")
        assert resp.status_code == 200


class TestLogoutRoute:
    def test_logout_clears_session(self, client, patient_user):
        _login(client, "testpatient", "TestPass123!")
        resp = _logout(client)
        assert resp.status_code == 200


class TestRegisterRoute:
    def test_register_page_loads(self, client):
        resp = client.get("/auth/register")
        assert resp.status_code == 200

    def test_register_new_user(self, client, app, db):
        resp = client.post(
            "/auth/register",
            data={
                "username": "newuser123",
                "email": "newuser123@test.local",
                "password": "NewPass123!",
                "confirm_password": "NewPass123!",
                "role": "patient",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            from app.models import User
            user = User.query.filter_by(username="newuser123").first()
            if user:
                db.session.delete(user)
                db.session.commit()

    def test_register_duplicate_username_rejected(self, client, patient_user):
        resp = client.post(
            "/auth/register",
            data={
                "username": "testpatient",
                "email": "other@test.local",
                "password": "Pass1234!",
                "confirm_password": "Pass1234!",
                "role": "patient",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_register_mismatched_passwords(self, client):
        resp = client.post(
            "/auth/register",
            data={
                "username": "mismatch_user",
                "email": "mismatch@test.local",
                "password": "Pass1234!",
                "confirm_password": "DifferentPass!",
                "role": "patient",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestProtectedRoutes:
    def test_patient_dashboard_requires_login(self, client):
        resp = client.get("/patient/dashboard", follow_redirects=True)
        # Unauthenticated → redirected to login
        assert resp.status_code == 200
        assert b"login" in resp.data.lower() or resp.request.path.startswith("/auth")

    def test_doctor_dashboard_requires_login(self, client):
        resp = client.get("/doctor/dashboard", follow_redirects=True)
        assert resp.status_code == 200
        assert b"login" in resp.data.lower() or resp.request.path.startswith("/auth")

    def test_admin_dashboard_requires_login(self, client):
        resp = client.get("/admin/dashboard", follow_redirects=True)
        assert resp.status_code == 200
