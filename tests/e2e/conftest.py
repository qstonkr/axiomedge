import os

import httpx
import pytest

API_URL = os.getenv("TEST_API_URL", "http://localhost:8000")


@pytest.fixture
def api_url():
    return API_URL


@pytest.fixture
def api():
    with httpx.Client(base_url=API_URL, timeout=120) as client:
        yield client
