"""
Pytest configuration and shared fixtures for the OSA Detection test suite.
"""
import os
import pytest

# Set required environment variables before any app import.
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("WTF_CSRF_ENABLED", "False")


@pytest.fixture(scope="session")
def app():
    """Create a Flask application configured for testing."""
    from app import create_app, db as _db

    class TestConfig:
        TESTING = True
        SECRET_KEY = "test-secret-key-not-for-production"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        WTF_CSRF_ENABLED = False
        WTF_CSRF_CHECK_DEFAULT = False
        SESSION_COOKIE_SECURE = False
        UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "_uploads_test")
        MAX_CONTENT_LENGTH = 10 * 1024 * 1024
        MAIL_SERVER = "localhost"
        MAIL_PORT = 25
        MAIL_USE_TLS = False
        MAIL_USERNAME = ""
        MAIL_PASSWORD = ""
        MAIL_DEFAULT_SENDER = "test@osa-test.local"

    application = create_app()
    application.config.from_object(TestConfig)

    with application.app_context():
        _db.create_all()
        yield application
        _db.drop_all()


@pytest.fixture(scope="function")
def db(app):
    """Provide a clean database for each test function."""
    from app import db as _db
    with app.app_context():
        yield _db
        _db.session.rollback()


@pytest.fixture(scope="function")
def client(app):
    """Flask test client with session support."""
    return app.test_client()


@pytest.fixture(scope="function")
def patient_user(db, app):
    """A confirmed patient User in the database."""
    from app.models import User
    with app.app_context():
        user = User(
            username="testpatient",
            email="patient@test.local",
            role="patient",
            is_active=True,
            is_email_confirmed=True,
        )
        user.set_password("TestPass123!")
        db.session.add(user)
        db.session.commit()
        yield user
        db.session.delete(user)
        db.session.commit()


@pytest.fixture(scope="function")
def doctor_user(db, app):
    """A confirmed, verified doctor User in the database."""
    from app.models import User
    with app.app_context():
        user = User(
            username="testdoctor",
            email="doctor@test.local",
            role="doctor",
            is_active=True,
            is_email_confirmed=True,
            verification_status="approved",
        )
        user.set_password("DoctorPass123!")
        db.session.add(user)
        db.session.commit()
        yield user
        db.session.delete(user)
        db.session.commit()
