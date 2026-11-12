import os
from pathlib import Path

import pytest
import requests

FIXTURES = Path(__file__).parent / "fixtures"


def make_response(status: int = 200, body: bytes = b"", content_type: str = "text/html") -> requests.Response:
    """Build an offline requests.Response, the way the real server would send it."""
    resp = requests.Response()
    resp.status_code = status
    resp._content = body
    resp.headers["Content-Type"] = content_type
    # Same as requests' HTTPAdapter: guess from the header (text/* with no charset gives ISO-8859-1).
    resp.encoding = requests.utils.get_encoding_from_headers(resp.headers)
    return resp


@pytest.fixture
def fixture_bytes():
    def _read(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()
    return _read


@pytest.fixture
def fixture_html(fixture_bytes):
    def _read(name: str) -> str:
        return fixture_bytes(name).decode("utf-8")
    return _read


# ---- PostgreSQL (DB tests skip when the P6 container is not reachable) ----------
TEST_DB = "scraper_test"


def missing(reason: str, require_var: str) -> None:
    """Skip locally, but FAIL when CI demands the dependency (e.g. REQUIRE_DB=1).

    This way CI can never go green by silently skipping the DB or browser tests.
    """
    if os.environ.get(require_var) == "1":
        pytest.fail(f"{reason} ({require_var}=1, so this is a failure, not a skip)", pytrace=False)
    pytest.skip(reason)


@pytest.fixture
def pg_conn():
    import psycopg
    from scraper.config import load_settings
    from scraper import db

    settings = load_settings()
    try:
        admin = db.connect(settings)
    except Exception as exc:  # noqa: BLE001
        missing(f"PostgreSQL not reachable: {exc}", "REQUIRE_DB")
    admin.autocommit = True
    if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)).fetchone():
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    admin.close()
    conn = db.connect(settings, dbname=TEST_DB)
    with conn.transaction():
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    db.init_schema(conn)
    yield conn
    conn.close()
