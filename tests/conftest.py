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
