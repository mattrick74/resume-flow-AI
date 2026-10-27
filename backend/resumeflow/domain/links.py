"""Contact links (LinkedIn, GitHub, portfolio): only real URLs, shown as the URL and clickable.

A label without its address ("LinkedIn") is not a link: a resume shows the address itself, e.g.
"linkedin.com/in/jane-doe", hyperlinked to https://www.linkedin.com/in/jane-doe.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_TLDS = "com|net|org|io|dev|me|co|ai|app|info|tech|site|page|us|uk|ca|de|fr|in|au|eu|xyz|blog|online|pro|cloud"
_URL = re.compile(
    rf"(?:https?://[^\s<>\"'|]+|www\.[^\s<>\"'|]+|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:{_TLDS})\b(?:/[^\s<>\"'|]*)?)",
    re.IGNORECASE,
)

# Sites whose links are profile links wherever they appear in a resume.
PROFILE_HOSTS = (
    "linkedin.com", "github.com", "gitlab.com", "bitbucket.org", "stackoverflow.com", "behance.net", "dribbble.com",
    "medium.com", "kaggle.com", "x.com", "twitter.com", "huggingface.co",
)


def find_url(value: str) -> str | None:
    """The web address in ``value`` ("LinkedIn: linkedin.com/in/jane" → "linkedin.com/in/jane"), or None."""
    for match in _URL.finditer(value or ""):
        start = match.start()
        if start > 0 and value[start - 1] == "@":
            continue  # the domain of an email address
        url = match.group(0).rstrip(".,;:)]}'\"")
        if "@" not in url:
            return url
    return None


def link_key(url: str) -> str:
    return re.sub(r"^(?:https?://)?(?:www\.)?", "", url.strip(), flags=re.IGNORECASE).rstrip("/").lower()


def clean_links(values: Iterable[str] | None) -> list[str]:
    """Only entries that contain a web address, once each (``http://x.com/a`` and ``x.com/a/`` are the same link)."""
    found: dict[str, str] = {}
    for value in values or []:
        url = find_url(value or "")
        if url and link_key(url) not in found:
            found[link_key(url)] = url
    return list(found.values())


def link_href(url: str) -> str:
    return url if re.match(r"^https?://", url, re.IGNORECASE) else f"https://{url}"


def link_text(url: str) -> str:
    """How a resume shows the address: without "https://", "www." or a trailing slash."""
    return re.sub(r"^(?:https?://)?(?:www\.)?", "", url.strip(), flags=re.IGNORECASE).rstrip("/")


def is_profile_link(url: str) -> bool:
    host = link_key(url).split("/", 1)[0]
    return any(host == h or host.endswith("." + h) for h in PROFILE_HOSTS)
