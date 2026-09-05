"""Tests for the nflreadpy download hardening.

nflverse serves its data through GitHub release downloads, which intermittently
answer 503. nflreadpy does not retry, so one of those anywhere in a page load
took the whole app down -- and because nflreadpy caches in memory only, every
restart was a fresh chance to hit one on a file downloaded successfully an hour
before.

These tests cover both halves of the fix, and the deliberate non-retry of 404,
which is what a season nflverse has not published yet returns.
"""

import http.server
import socketserver
import threading

import pytest
import requests

from nflreadpy.config import CacheMode, get_config
from nflreadpy.downloader import get_downloader
from repositories.nflreadpy_setup import RETRY_STATUSES, configure

configure()


class _Flaky(http.server.BaseHTTPRequestHandler):
    """Answers with a scripted list of status codes, one per request."""

    statuses = []
    seen = []

    def do_GET(self):
        index = len(self.seen)
        self.seen.append(index)
        status = self.statuses[min(index, len(self.statuses) - 1)]
        self.send_response(status)
        self.end_headers()
        if status == 200:
            self.wfile.write(b"ok")

    def log_message(self, *args):
        """Silence the handler's stderr logging, which pytest would capture."""


def serve(statuses):
    """Run a throwaway HTTP server that answers with `statuses` in order.

    Steps:
        1. Point the handler at this test's status script and reset its counter.
        2. Bind a server on port 0, which asks the OS for any free port.
        3. Serve on a background thread so the test can make requests.

    Args:
        statuses: HTTP status codes to answer with, one per request. The last
            one repeats if more requests arrive than there are entries.

    Returns:
        tuple: `(server, url)`. Shut the server down when finished.
    """
    _Flaky.statuses = list(statuses)
    _Flaky.seen = []
    server = socketserver.TCPServer(("127.0.0.1", 0), _Flaky)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/data.parquet"


# --------------------------------------------------------------------------
# caching
# --------------------------------------------------------------------------

def test_cache_is_on_the_filesystem():
    # In memory-only mode every restart re-downloads ~32 MB, and load_ff_playerids
    # alone has been measured at two minutes.
    assert get_config().cache_mode == CacheMode.FILESYSTEM


# --------------------------------------------------------------------------
# retries
# --------------------------------------------------------------------------

def test_a_transient_503_is_retried_rather_than_raised():
    # The exact failure: GitHub 503s, then serves the file a moment later.
    server, url = serve([503, 503, 200])
    try:
        response = get_downloader().session.get(url, timeout=10)
    finally:
        server.shutdown()

    assert response.status_code == 200
    assert len(_Flaky.seen) == 3, "expected two failures then a success"


def test_a_persistent_outage_still_fails():
    # Retrying is not the same as pretending. If nflverse is genuinely down the
    # caller must still find out, rather than get an empty frame.
    server, url = serve([503])
    try:
        with pytest.raises(requests.exceptions.RequestException):
            get_downloader().session.get(url, timeout=10)
    finally:
        server.shutdown()


def test_a_404_is_not_retried():
    # LOAD-BEARING. nflverse returns 404 for a season it has not published yet,
    # which is how DFS_SEASONS finds out the current season has no games. That is
    # a real answer, so retrying it would only add delay before the same result.
    server, url = serve([404])
    try:
        response = get_downloader().session.get(url, timeout=10)
    finally:
        server.shutdown()

    assert response.status_code == 404
    assert len(_Flaky.seen) == 1, "404 should be taken at face value"
    assert 404 not in RETRY_STATUSES


def test_configure_is_idempotent():
    # Called at import by both repositories, so it runs more than once.
    before = len(get_downloader().session.adapters)
    configure()
    configure()
    assert len(get_downloader().session.adapters) == before
