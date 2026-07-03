"""News ingestion: RSS multi-feed poller with dedupe and spam guard.

v1 sources are RSS only (no auth, no sessions). A Telegram-channel source can be
plugged in later by adapting the operator's existing whalecoin-bot reader — the
Headline shape is compatible by design.
"""

from __future__ import annotations

import time
from calendar import timegm
from dataclasses import dataclass, field
from typing import Iterable, Optional

import feedparser
import httpx

from .models import Headline, normalize

MAX_HEADLINE_AGE_S = 48 * 3600  # entries older than this are archive, not news

DEFAULT_FEEDS = [
    # name, url — crypto + macro/politics skew, matching Polymarket's market mix
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("The Block", "https://www.theblock.co/rss.xml"),
    ("SEC Press", "https://www.sec.gov/newsroom/press-releases/rss"),
    ("BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC Politics", "http://feeds.bbci.co.uk/news/politics/rss.xml"),
    ("ESPN", "https://www.espn.com/espn/rss/news"),
]

# spam guard borrowed from the operator's whalecoin-bot config
MUTE_KEYWORDS = [
    "airdrop", "giveaway", "join now", "referral", "presale", "whitelist",
    "t.me/+", "dm me", "sponsored", "press release:", "partner content",
]


def is_muted(text: str, mute_keywords: Optional[list[str]] = None) -> bool:
    t = (text or "").lower()
    return any(k in t for k in (mute_keywords or MUTE_KEYWORDS))


def _entry_age_s(e) -> Optional[float]:
    """Age in seconds from the entry's published/updated struct_time, if present."""
    st = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
    if not st:
        return None
    try:
        return time.time() - timegm(st)
    except (TypeError, ValueError, OverflowError):
        return None


def parse_feed(name: str, url: str, timeout: float = 15.0,
               max_age_s: float = MAX_HEADLINE_AGE_S) -> list[Headline]:
    """Fetch one RSS/Atom feed -> fresh Headlines.

    We fetch with httpx ourselves (feedparser's own fetch has NO timeout — one stalled
    feed would hang the whole poll loop forever) and drop entries older than max_age_s:
    feeds retain weeks of archive, and archive posted as breaking news is the single
    most embarrassing public failure this bot could have.
    """
    resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                     headers={"User-Agent": "newsbet/0.1"})
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    out: list[Headline] = []
    for e in parsed.entries:
        title = (getattr(e, "title", "") or "").strip()
        if not title:
            continue
        age = _entry_age_s(e)
        if age is not None and age > max_age_s:
            continue  # archive, not news
        summary = (getattr(e, "summary", "") or "")[:500]
        out.append(Headline(
            source=name,
            source_type="rss",
            title=title,
            summary=summary,
            url=(getattr(e, "link", "") or "").strip(),
            published_at=getattr(e, "published", None) or getattr(e, "updated", None),
        ))
    return out


@dataclass
class Ingestor:
    """Polls feeds, yields only headlines not seen before (per fingerprint)."""

    feeds: list = field(default_factory=lambda: list(DEFAULT_FEEDS))
    mute_keywords: list = field(default_factory=lambda: list(MUTE_KEYWORDS))
    seen: set = field(default_factory=set)  # fingerprints (persisted by the store)

    def poll(self) -> list[Headline]:
        fresh: list[Headline] = []
        for name, url in self.feeds:
            try:
                entries = parse_feed(name, url)
            except Exception:
                continue  # a broken feed must never kill the poll cycle
            for h in entries:
                fp = h.fingerprint()
                if fp in self.seen:
                    continue
                self.seen.add(fp)
                if is_muted(h.text(), self.mute_keywords):
                    continue
                if not normalize(h.title):
                    continue
                fresh.append(h)
        return fresh
