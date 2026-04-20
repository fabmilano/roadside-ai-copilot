import sys
from pathlib import Path

# Add backend root to path so imports work when running pytest from backend/
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from session import create_session, sessions


SAMPLE_CUSTOMER = {
    "id": "C001",
    "name": "Sarah Mitchell",
    "policy_number": "ALC-10042",
    "tier": "gold",
    "vehicle": {"make": "Ford", "model": "Focus", "year": 2021, "reg": "AB21 CDE"},
}

COMMERCIAL_CUSTOMER = {
    "id": "C006",
    "name": "Michael Nowak",
    "policy_number": "ALC-60099",
    "tier": "bronze",
    "vehicle": {"make": "Fiat", "model": "500", "year": 2018, "reg": "AB18 CDF"},
    "notes": "Vehicle registered as Uber driver - commercial use",
}


@pytest.fixture
def sample_customer():
    return SAMPLE_CUSTOMER.copy()


@pytest.fixture
def commercial_customer():
    return COMMERCIAL_CUSTOMER.copy()


@pytest.fixture
def sample_session():
    session_id = "test-session-001"
    sessions.pop(session_id, None)  # clean up if lingering
    create_session(session_id)
    yield sessions[session_id]
    sessions.pop(session_id, None)
