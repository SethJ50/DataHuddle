"""Makes nflreadpy survive a flaky nflverse, and stop re-downloading everything.

nflreadpy's defaults are wrong for an app that gets restarted a lot:

1. IT CACHES IN MEMORY ONLY. Every `streamlit run` re-downloads every file --
   about 32 MB, and `load_ff_playerids` alone has been measured at two minutes.
2. IT DOES NOT RETRY. nflverse publishes through GitHub release downloads, which
   intermittently answer 503. One of those anywhere in a page load takes the
   whole app down with a traceback, even though a retry a second later succeeds.

Point 2 is made worse by point 1: without a disk cache, every restart is a fresh
chance to hit a 503 on a file that was downloaded successfully an hour ago.

`configure()` fixes both. It is called at import time by the two repositories
that use nflreadpy, so anything reaching NFL data has already been through it.
"""

import os

from nflreadpy.config import CacheMode, update_config
from nflreadpy.downloader import get_downloader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

RETRY_STATUSES = (429, 500, 502, 503, 504)
"""HTTP responses worth trying again. All of these mean "not right now" rather
than "no": 429 is rate limiting, and the 5xx family is the server having a bad
moment. A 404 is deliberately NOT here -- nflverse returns one for a season it
has not published yet, and retrying that just delays a real answer."""

RETRY_ATTEMPTS = 4
"""How many times to try before giving up. Waits 0s, 1s, 2s, then 4s between
attempts, so a transient outage costs about seven seconds instead of a crash."""

BACKOFF_FACTOR = 1.0
"""Multiplier on the doubling wait between attempts. urllib3 also honours a
Retry-After header when the server sends one, which is what 429 responses do."""

_configured = False


def configure():
    """Point nflreadpy at a disk cache and teach it to retry a failed download.

    Called once, at import, by the repositories that read NFL data. Safe to call
    again -- the second call does nothing.

    Steps:
        1. Return immediately if this already ran.
        2. Switch caching to the filesystem, unless NFLREADPY_CACHE is set in the
           environment, in which case that wins. nflreadpy picks the cache
           directory itself (`~/Library/Caches/nflreadpy` on a Mac);
           NFLREADPY_CACHE_DIR overrides it if you want it somewhere specific.
        3. Build a Retry policy covering the status codes above.
        4. Mount it on the downloader's requests Session for both http and
           https, so every nflreadpy download retries without any of them
           needing to know about it.

    Returns:
        None: The effect is on nflreadpy's global config and its shared
            downloader, both of which every later call reads.

    Note:
        THE ENVIRONMENT WINS. Setting NFLREADPY_CACHE explicitly -- including to
        "memory" or "off" -- is respected, so a deployment that wants different
        behaviour, or a test that wants no cache at all, can still say so.

        The retry is mounted on nflreadpy's OWN session object rather than
        wrapping its functions. `get_downloader` hands back a module-level
        singleton built at import, so mounting an adapter on it covers every
        download in the process, including ones this app never calls directly.

        Cache entries expire after 24 hours by default, which is right during
        the season: stats change weekly, and a day-old copy is never far wrong.
        NFLREADPY_CACHE_DURATION takes a number of seconds if you want longer.
    """
    global _configured
    if _configured:
        return

    # An explicit setting is a decision; only fill in the default.
    if not os.environ.get("NFLREADPY_CACHE"):
        # Both imported from nflreadpy.config rather than the package root,
        # which does not re-export them. Passing the CacheMode member rather
        # than the string "filesystem" because update_config stores whatever it
        # is given without coercing it.
        update_config(cache_mode=CacheMode.FILESYSTEM)

    policy = Retry(
        total=RETRY_ATTEMPTS,
        status_forcelist=RETRY_STATUSES,
        backoff_factor=BACKOFF_FACTOR,
        allowed_methods=frozenset(["GET"]),
        raise_on_status=True,
    )

    adapter = HTTPAdapter(max_retries=policy)
    session = get_downloader().session
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    _configured = True
