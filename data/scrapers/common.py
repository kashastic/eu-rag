"""Shared polite-scraping infrastructure (docs/DATA_SOURCES.md rule 3).

Every scraper fetches through PoliteFetcher: robots.txt is checked per host
(and its crawl-delay honored), requests are rate-limited, responses are
cached under data/raw/ so re-chunking never re-fetches, and the User-Agent
identifies the project with a contact address.
"""

import logging
import ssl
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

RAW_ROOT = Path(__file__).resolve().parents[1] / "raw"
USER_AGENT = (
    "EURAG-corpus-builder/0.1 (citation-first RAG for EU SME compliance; "
    "contact: akash1acharya@gmail.com)"
)


def ssl_context() -> ssl.SSLContext:
    """macOS Python installs often lack a CA bundle; use certifi's when
    available (it ships with our httpx/anthropic deps)."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class RobotsDisallowed(PermissionError):
    """robots.txt forbids fetching this URL for our User-Agent."""


class PoliteFetcher:
    def __init__(self, cache_dir: Path, min_interval: float = 2.0):
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request = 0.0

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser:
        host = urlparse(url).netloc
        if host not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            robots_url = f"{urlparse(url).scheme}://{host}/robots.txt"
            try:
                with urlopen(
                    Request(robots_url, headers={"User-Agent": USER_AGENT}),
                    timeout=20,
                    context=ssl_context(),
                ) as response:
                    parser.parse(response.read().decode("utf-8", "replace").splitlines())
            except Exception:
                parser.parse([])  # unreachable robots.txt → assume allowed
            self._robots[host] = parser
        return self._robots[host]

    def fetch(self, url: str, cache_key: str, *, force: bool = False) -> str:
        cache_path = self.cache_dir / cache_key
        if cache_path.is_file() and not force:
            return cache_path.read_text(encoding="utf-8", errors="replace")

        robots = self._robots_for(url)
        if not robots.can_fetch(USER_AGENT, url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        delay = max(self.min_interval, robots.crawl_delay(USER_AGENT) or 0)
        wait = delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

        logger.info("fetching %s", url)
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60, context=ssl_context()) as response:
            body = response.read().decode("utf-8", errors="replace")
        self._last_request = time.monotonic()

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(body, encoding="utf-8")
        return body
