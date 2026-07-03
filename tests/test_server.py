"""Offline tests: tool logic against a fake index (no network)."""

import pytest

import polymarket_news_mcp.server as srv
from polymarket_news_mcp.matching import MarketIndex
from polymarket_news_mcp.models import Market


def mk(question, event_slug="ev", event_title="", vol=10000, mid="1"):
    return Market(
        id=mid, question=question, slug=f"s-{mid}", event_slug=event_slug,
        event_title=event_title, condition_id="0x1", outcomes=["Yes", "No"],
        outcome_prices=[0.4, 0.6], volume_24h=vol, liquidity=50000,
        end_date="2027-01-01T00:00:00Z",
    )


@pytest.fixture(autouse=True)
def fake_index(monkeypatch):
    markets = [
        mk("Fed rate hike in 2026?", "fed-hike", "Fed rates", vol=8e5, mid="1"),
        mk("Will Bitcoin reach $120,000 by December 31?", "btc-120k", "Bitcoin", vol=1e6, mid="2"),
        mk("Will Andy Burnham be the next Prime Minister?", "uk-pm", "UK politics", vol=5e5, mid="3"),
    ] + [mk(f"Filler about topic {i}?", f"f{i}", mid=str(100 + i)) for i in range(30)]
    idx = MarketIndex.build(markets)
    monkeypatch.setattr(srv, "_market_index", lambda: idx)
    return idx


def test_match_news_finds_fed_market():
    out = srv.match_news("Federal Reserve signals rate hike as inflation stays hot")
    assert out and out[0]["question"].startswith("Fed rate hike")
    assert out[0]["match_score"] > 0
    assert "fed" in out[0]["matched_terms"] or "hike" in out[0]["matched_terms"]
    assert out[0]["outcomes"] == {"Yes": 0.4, "No": 0.6}


def test_match_news_returns_empty_for_unrelated():
    assert srv.match_news("Local bakery wins croissant award in Lyon") == []


def test_search_markets_is_lenient():
    out = srv.search_markets("bitcoin")
    assert out and "Bitcoin" in out[0]["question"]


def test_trending_sorted_by_volume():
    out = srv.trending_markets(top_k=3)
    vols = [d["volume_24h_usd"] for d in out]
    assert vols == sorted(vols, reverse=True)
    assert out[0]["question"].startswith("Will Bitcoin")  # highest vol in fixture


def test_get_market_by_slug_and_fallback():
    out = srv.get_market("uk-pm")
    assert "Prime Minister" in out["question"]
    out2 = srv.get_market("https://polymarket.com/event/uk-pm")
    assert out2["question"] == out["question"]
    assert "error" in srv.get_market("zzz-nonexistent-xyz-qq")


def test_tools_registered():
    # the five public tools exist on the FastMCP instance
    names = set(srv.mcp._tool_manager._tools.keys())
    assert names == {"match_news", "search_markets", "trending_markets",
                     "get_market", "latest_matched_news"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
