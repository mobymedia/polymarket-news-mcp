"""Polymarket market index via the Gamma API (read-only, no auth).

Fetches all active markets, slims them to Match-relevant fields, and caches to disk
so the matcher can rebuild instantly between poll cycles. Field names verified live
against gamma-api.polymarket.com (July 2026): question, slug, description,
events[].slug/title, volume24hr, liquidity, outcomes, outcomePrices, endDate,
restricted, conditionId.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import httpx

from .models import Market


def is_expired(m: Market, now: Optional[datetime] = None) -> bool:
    """True if the market's endDate has passed (news can't move a decided market).
    Defensive against dirty data: unparseable or tz-naive dates must never raise."""
    if not m.end_date:
        return False
    try:
        end = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return end <= (now or datetime.now(timezone.utc))
    except (ValueError, TypeError):
        return False

GAMMA = "https://gamma-api.polymarket.com"
PAGE_SIZE = 100  # Gamma caps limit at 100 (verified live: asking 500 returns 100)
MAX_OFFSET = 2000  # Gamma 422s past offset 2000 (verified live) -> top-2k by liquidity


def _to_float(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _parse_json_list(x) -> list:
    """Gamma returns some list fields as JSON-encoded strings."""
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        try:
            v = json.loads(x)
            return v if isinstance(v, list) else []
        except json.JSONDecodeError:
            return []
    return []


def parse_market(raw: dict) -> Optional[Market]:
    question = (raw.get("question") or "").strip()
    if not question:
        return None
    events = raw.get("events") or []
    ev = events[0] if events else {}
    outcomes = [str(o) for o in _parse_json_list(raw.get("outcomes"))]
    prices = [_to_float(p) for p in _parse_json_list(raw.get("outcomePrices"))]
    return Market(
        id=str(raw.get("id", "")),
        question=question,
        slug=raw.get("slug", "") or "",
        event_slug=(ev.get("slug") or "") if isinstance(ev, dict) else "",
        event_title=(ev.get("title") or "").strip() if isinstance(ev, dict) else "",
        condition_id=raw.get("conditionId", "") or "",
        outcomes=outcomes,
        outcome_prices=prices,
        volume_24h=_to_float(raw.get("volume24hr")),
        liquidity=_to_float(raw.get("liquidity")),
        end_date=raw.get("endDate", "") or "",
        description=(raw.get("description") or "")[:500],
        restricted=bool(raw.get("restricted", False)),
    )


def fetch_active_markets(
    client: Optional[httpx.Client] = None,
    min_liquidity: float = 1000.0,
) -> list[Market]:
    """The most liquid active, order-book-enabled markets (top ~2k by liquidity).

    Gamma hard-caps pagination at offset 2000, so we order by liquidity descending and
    take what it allows — which is the right universe anyway: a headline matching an
    illiquid market isn't actionable for readers. The floor drops dead-tail noise.
    """
    owns = client is None
    client = client or httpx.Client(timeout=30.0)
    out: list[Market] = []
    try:
        for offset in range(0, MAX_OFFSET + 1, PAGE_SIZE):
            resp = client.get(
                f"{GAMMA}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "enableOrderBook": "true",
                    "order": "liquidityNum",  # numeric sort ('liquidity' sorts lexically!)
                    "ascending": "false",
                    "limit": PAGE_SIZE,
                    "offset": offset,
                },
            )
            if resp.status_code == 422:  # past the API's pagination window
                break
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            hit_floor = False
            for raw in batch:
                m = parse_market(raw)
                if m is None:
                    continue
                if m.liquidity < min_liquidity:
                    hit_floor = True  # liquidity-ordered -> everything after is below floor
                    continue
                if is_expired(m):
                    continue  # endDate passed but Gamma still lists it active
                out.append(m)
            if hit_floor or len(batch) < PAGE_SIZE:
                break
    finally:
        if owns:
            client.close()
    return out


# --- disk cache --------------------------------------------------------------

def save_index(markets: Iterable[Market], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": time.time(),
        "markets": [m.__dict__ for m in markets],
    }
    path.write_text(json.dumps(payload))


import dataclasses

_MARKET_FIELDS = {f.name for f in dataclasses.fields(Market)}


def load_index(path: str | Path, max_age_s: float = 0) -> Optional[list[Market]]:
    """Load cached index; None if missing, stale, or unusable (schema drift must
    read as a cache miss so a fresh fetch overwrites it — never a crash loop)."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
        if max_age_s and (time.time() - payload.get("fetched_at", 0)) > max_age_s:
            return None
        return [
            Market(**{k: v for k, v in d.items() if k in _MARKET_FIELDS})
            for d in payload.get("markets", [])
        ]
    except (json.JSONDecodeError, OSError, TypeError, KeyError, AttributeError):
        return None


def get_index(
    cache_path: str | Path,
    max_age_s: float = 900,
    min_liquidity: float = 1000.0,
) -> list[Market]:
    """Cached-or-fresh index: refetch when the cache is older than max_age_s.
    A failed fetch (network, 5xx, HTML-instead-of-JSON) falls back to the stale cache —
    matching yesterday's markets beats matching nothing."""
    cached = load_index(cache_path, max_age_s=max_age_s)
    if cached is not None:
        return cached
    try:
        markets = fetch_active_markets(min_liquidity=min_liquidity)
    except Exception:
        markets = []
    if markets:
        save_index(markets, cache_path)
        return markets
    return load_index(cache_path) or []
