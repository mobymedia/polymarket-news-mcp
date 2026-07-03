"""Core data shapes: headlines in, markets indexed, matches out.

Conventions follow the operator's existing whalecoin-bot (normalize -> bag of words,
sha256 fingerprint for dedupe) so the two pipelines can interoperate later.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

_URL = re.compile(r"https?://\S+|t\.me/\S+|@\w+")
_NONWORD = re.compile(r"[^a-z0-9. ]+")  # dots survive here; stray ones stripped below
_DOT_NOT_DECIMAL = re.compile(r"(?<!\d)\.|\.(?!\d)")  # any dot not between digits
_WS = re.compile(r"\s+")

# words that carry no matching signal in either headlines or market questions
STOPWORDS = frozenset(
    """a an the and or but if then else of in on at to for from by with as is are was
    were be been being will would could should may might shall can do does did not no
    yes this that these those it its it's his her their our your my he she they we you
    i who what when where which how why whose than more most other some any all each
    both few many much own same so too very just about above after again against
    before below between during into over under until up down out off once here there
    new says say said announces announced announcement reports reported reportedly
    breaking update news latest today yesterday tomorrow week month year 2024 2025
    2026 2027 2028 amid despite following ahead near set plans plan expected expects
    make makes made take takes took get gets got
    will has have had was be end start day days hit hits taps vs per top next first
    last biggest largest higher lower highest lowest average daily weekly monthly
    country another best won wins win""".split()
)


_NUM_COMMA = re.compile(r"(?<=\d),(?=\d{3}\b)")  # commas only — never real decimals


def normalize(text: str) -> str:
    """Lowercase, accent-fold, strip links/handles/punctuation -> bag of words.
    Number handling: thousand separators collapse ('$120,000' -> 120000, matching the
    120k shorthand) while decimals survive ('$1.5b' stays one token for norm_token to
    expand); accents fold so Erdoğan matches the ASCII 'Erdogan' Polymarket writes."""
    t = (text or "").lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = _URL.sub(" ", t)
    t = _NUM_COMMA.sub("", t)
    t = _NONWORD.sub(" ", t)
    t = _DOT_NOT_DECIMAL.sub(" ", t)
    return _WS.sub(" ", t).strip()


def token_seq(text: str) -> list[str]:
    """Normalized, stopword-filtered token SEQUENCE (repeats kept, for bigrams —
    deduping first would fabricate adjacencies and lose real repeated-entity phrases)."""
    return [w for w in normalize(text).split() if len(w) >= 2 and w not in STOPWORDS]


def tokens(text: str) -> list[str]:
    """Normalized, stopword-filtered tokens (order-preserving, deduped)."""
    seen: set[str] = set()
    out: list[str] = []
    for w in token_seq(text):
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


@dataclass
class Headline:
    source: str  # "CoinDesk", "@WatcherGuru", ...
    source_type: str  # rss | telegram | manual
    title: str
    summary: str = ""
    url: str = ""
    published_at: Optional[str] = None  # ISO string as given by the feed

    def text(self) -> str:
        return f"{self.title} {self.summary}".strip()

    def fingerprint(self) -> str:
        basis = (self.url or "").strip().lower() or normalize(self.text())
        return hashlib.sha256(basis.encode()).hexdigest()[:32]


@dataclass
class Market:
    """One Polymarket market, slimmed from the Gamma API for indexing."""

    id: str
    question: str
    slug: str
    event_slug: str
    event_title: str
    condition_id: str
    outcomes: list[str] = field(default_factory=list)
    outcome_prices: list[float] = field(default_factory=list)
    volume_24h: float = 0.0
    liquidity: float = 0.0
    end_date: str = ""  # ISO
    description: str = ""
    restricted: bool = False

    @property
    def url(self) -> str:
        """Public deep link. Event pages are canonical on polymarket.com."""
        if self.event_slug:
            return f"https://polymarket.com/event/{self.event_slug}"
        return f"https://polymarket.com/market/{self.slug}"

    def match_text(self) -> str:
        """Text the matcher indexes. Question + event title carry the signal;
        description is noisy boilerplate so it is excluded by default."""
        return f"{self.question} {self.event_title}"

    def yes_price(self) -> Optional[float]:
        """Price of the first outcome (Yes for binary markets), if known."""
        return self.outcome_prices[0] if self.outcome_prices else None


@dataclass
class Match:
    market: Market
    score: float
    matched_terms: list[str] = field(default_factory=list)


@dataclass
class Card:
    """A composed push: one headline + its matched markets, ready to send."""

    headline: Headline
    matches: list[Match]
    composed_text: str = ""  # final Telegram HTML
