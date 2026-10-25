"""
Test configuration & fixtures.
"""

import os

import pytest

# Force test configuration before any app imports
os.environ["ENV"] = "dev"
os.environ["GROQ_API_KEY"] = "mock-groq-key"
os.environ["JWT_SECRET"] = "test-secret-key-at-least-32-chars-long"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OTP_PROVIDER"] = "dev"
os.environ["DEV_OTP"] = "123456"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as db_module
from app.database import Base
from app.main import app
from app.routes.auth import _issue_token

# Test in-memory engine
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    """Set up a fresh in-memory database schema for each test."""
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestingSessionLocal)
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client():
    """FastAPI TestClient."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Valid JWT auth header fixture."""
    phone = "+919876543210"
    token = _issue_token(phone)
    return {"Authorization": f"Bearer {token}"}
