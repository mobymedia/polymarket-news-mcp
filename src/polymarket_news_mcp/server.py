"""polymarket-news-mcp — give an AI agent a headline, get the markets it moves.

An MCP server exposing news→prediction-market matching for Polymarket. Unlike the
many market-lookup MCP servers, the core tool here answers a different question:
"this just happened — where can the world's belief about it be read or traded?"

Matching is deterministic and explainable (IDF-weighted term overlap with a salience
gate — every match reports the terms that fired), built on the public Gamma API.
Read-only: no keys, no trading, no custody. Links and odds only.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .ingest import DEFAULT_FEEDS, Ingestor
from .markets import get_index
from .matching import MarketIndex
from .models import Headline, Market

CACHE_DIR = Path.home() / ".cache" / "polymarket-news-mcp"
INDEX_MAX_AGE_S = 900  # market index refreshes every 15 minutes
MATCH_THRESHOLD = 10.0  # strict: only confident matches (tuned on live eval)
SEARCH_THRESHOLD = 2.0  # lenient: search should recall broadly; the salience gate
                        # (not the threshold) is what keeps junk out of search results

mcp = FastMCP(
    "polymarket-news",
    instructions=(
        "Tools for connecting real-world news to Polymarket prediction markets. "
        "Use match_news to find the markets a headline could move, search_markets "
        "for free-text market search, latest_matched_news for a live news-to-market "
        "feed, trending_markets for what's moving now. All data is public/read-only; "
        "odds are live prices in [0,1]."
    ),
)

_index: Optional[MarketIndex] = None
_index_at: float = 0.0


def _market_index() -> MarketIndex:
    global _index, _index_at
    if _index is None or (time.time() - _index_at) > INDEX_MAX_AGE_S:
        markets = get_index(CACHE_DIR / "markets.json", max_age_s=INDEX_MAX_AGE_S)
        if markets:
            _index = MarketIndex.build(markets)
            _index_at = time.time()
    if _index is None:
        raise RuntimeError("Polymarket market index unavailable (network down, no cache)")
    return _index


def _market_dict(m: Market, score: Optional[float] = None,
                 matched_terms: Optional[list] = None) -> dict:
    d = {
        "question": m.question,
        "url": m.url,
        "event": m.event_title,
        "outcomes": dict(zip(m.outcomes, m.outcome_prices)),
        "volume_24h_usd": round(m.volume_24h, 2),
        "liquidity_usd": round(m.liquidity, 2),
        "end_date": m.end_date,
    }
    if score is not None:
        d["match_score"] = score
        d["matched_terms"] = matched_terms or []
    return d


@mcp.tool()
def match_news(text: str, top_k: int = 5) -> list[dict]:
    """Find the Polymarket markets a news headline or snippet could move.

    Pass a headline (optionally with a sentence or two of body text). Returns
    matched markets with live odds, 24h volume, deep links, a match_score
    (higher = stronger term overlap; >=15 is a confident match), and the exact
    matched_terms that fired, so you can judge the match yourself.
    """
    idx = _market_index()
    h = Headline(source="agent", source_type="manual", title=text)
    matches = idx.match(h, top_k=top_k, threshold=MATCH_THRESHOLD)
    return [_market_dict(mt.market, mt.score, mt.matched_terms) for mt in matches]


@mcp.tool()
def search_markets(query: str, top_k: int = 10) -> list[dict]:
    """Free-text search over active Polymarket markets (lenient matching).

    Use for questions like 'what markets exist about the Fed' — returns the
    most relevant active markets with live odds and links.
    """
    idx = _market_index()
    h = Headline(source="agent", source_type="manual", title=query)
    # min_terms=1: single-word queries ("bitcoin", "fed") are normal for search
    matches = idx.match(h, top_k=top_k, threshold=SEARCH_THRESHOLD, min_terms=1)
    return [_market_dict(mt.market, mt.score, mt.matched_terms) for mt in matches]


@mcp.tool()
def trending_markets(top_k: int = 10) -> list[dict]:
    """The most active Polymarket markets right now, by 24h volume."""
    idx = _market_index()
    top = sorted(idx.markets, key=lambda m: m.volume_24h, reverse=True)[:top_k]
    return [_market_dict(m) for m in top]


@mcp.tool()
def get_market(slug_or_url: str) -> dict:
    """Look up one market by its polymarket.com URL, event slug, or market slug."""
    idx = _market_index()
    q = slug_or_url.strip().lower().rstrip("/").split("/")[-1]
    for m in idx.markets:
        if q in (m.slug.lower(), m.event_slug.lower(), m.id):
            return _market_dict(m)
    # fall back to lenient search so near-miss slugs still resolve
    h = Headline(source="agent", source_type="manual", title=q.replace("-", " "))
    matches = idx.match(h, top_k=1, threshold=SEARCH_THRESHOLD)
    if matches:
        return _market_dict(matches[0].market, matches[0].score, matches[0].matched_terms)
    return {"error": f"no active market found for '{slug_or_url}'"}


@mcp.tool()
def latest_matched_news(top_k: int = 10, max_age_hours: float = 12.0) -> list[dict]:
    """Live feed: recent headlines from major news sources, each matched to the
    Polymarket markets it could move. The 'what does the news mean for the odds'
    view. Only headlines with at least one confident market match are returned.
    """
    idx = _market_index()
    ing = Ingestor()
    headlines = []
    for name, url in DEFAULT_FEEDS:
        try:
            from .ingest import parse_feed
            headlines.extend(parse_feed(name, url, max_age_s=max_age_hours * 3600))
        except Exception:
            continue  # one dead feed must not kill the tool
    out = []
    seen = set()
    for h in headlines:
        fp = h.fingerprint()
        if fp in seen:
            continue
        seen.add(fp)
        matches = idx.match(h, top_k=3, threshold=MATCH_THRESHOLD)
        if not matches:
            continue
        out.append({
            "headline": h.title,
            "source": h.source,
            "published_at": h.published_at,
            "news_url": h.url,
            "markets": [_market_dict(mt.market, mt.score, mt.matched_terms)
                        for mt in matches],
        })
    out.sort(key=lambda x: -max(m["match_score"] for m in x["markets"]))
    return out[:top_k]


# Opt-in trading (v0.2.0): tools only exist when the user configures their own key.
# Read-only remains the default experience. See trading.py for the full design.
import os as _os

if _os.environ.get("POLYMARKET_PRIVATE_KEY"):
    try:
        from .trading import register_trading_tools

        register_trading_tools(mcp)
    except ImportError:
        import sys as _sys

        print("POLYMARKET_PRIVATE_KEY is set but the trading extra is not installed: "
              "pip install 'polymarket-news-mcp[trading]'", file=_sys.stderr)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="polymarket-news MCP server")
    ap.add_argument("--http", action="store_true",
                    help="serve over streamable HTTP instead of stdio (for remote hosting)")
    ap.add_argument("--port", type=int, default=8000, help="HTTP port (with --http)")
    args = ap.parse_args()
    if args.http:
        mcp.settings.port = args.port
        mcp.settings.host = "0.0.0.0"
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
