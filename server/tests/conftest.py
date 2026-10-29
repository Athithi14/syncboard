import os
import tempfile

# Use a throwaway data folder so tests never touch real saved boards.
os.environ.setdefault("SYNCBOARD_DATA_DIR", tempfile.mkdtemp())

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)
