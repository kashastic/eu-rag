"""Verify every source_url in the corpus still resolves (citations must not
point at dead pages).

Usage: python -m infra.scripts.check_links [--registry var/registry.sqlite3]
Exits non-zero if any link is broken. Read-only; safe while the API runs.
"""

import argparse
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request

USER_AGENT = (
    "EURAG-corpus-builder/0.1 (link check; contact: akash1acharya@gmail.com)"
)


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def check(url: str) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0  # network-level failure


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", default="var/registry.sqlite3")
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{args.registry}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT DISTINCT source_url, title FROM documents"
        " WHERE source_url != '' ORDER BY source_url"
    ).fetchall()
    conn.close()

    broken = 0
    for url, title in rows:
        status = check(url)
        ok = 200 <= status < 400
        broken += not ok
        print(f"  {'ok ' if ok else 'BROKEN'} {status or 'ERR':>4}  {url}  [{title[:50]}]")
        time.sleep(3 if "eur-lex" in url else 1)

    print(f"\n{len(rows) - broken}/{len(rows)} links ok.")
    if broken:
        sys.exit(f"{broken} broken source link(s) — fix provenance before shipping.")


if __name__ == "__main__":
    main()
