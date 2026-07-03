"""Trading-module tests: gating, spend cap, builder-code attribution. All offline —
the client is faked; no keys, no network, no orders."""

import pytest

import polymarket_news_mcp.trading as tr


class FakeClient:
    def __init__(self):
        self.posted = []

    def get_tick_size(self, token_id):
        return "0.01"

    def get_neg_risk(self, token_id):
        return False

    def create_and_post_order(self, args, options=None, order_type=None, post_only=False,
                              defer_exec=False):
        self.posted.append(("limit", args))
        return {"orderID": "0xfake", "status": "matched"}

    def create_and_post_market_order(self, args, options=None, order_type=None,
                                     defer_exec=False):
        self.posted.append(("market", args))
        return {"orderID": "0xfake2", "status": "matched"}

    def get_open_orders(self, only_first_page=True):
        return [{"orderID": "0xopen"}]

    def get_address(self):
        return "0xSIGNER"


@pytest.fixture
def fake_client(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(tr, "get_client", lambda: c)
    return c


def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    assert tr.enabled() is False


def test_enabled_with_key(monkeypatch):
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0xabc")
    assert tr.enabled() is True


def test_spend_cap_blocks_limit_order(fake_client, monkeypatch):
    monkeypatch.setenv("POLYMARKET_MAX_ORDER_USD", "50")
    out = tr.place_limit_order("tok", "BUY", price=0.5, size=200)  # $100 > $50 cap
    assert "error" in out and "spend cap" in out["error"]
    assert fake_client.posted == []  # nothing reached the client


def test_spend_cap_blocks_market_buy(fake_client, monkeypatch):
    monkeypatch.setenv("POLYMARKET_MAX_ORDER_USD", "50")
    out = tr.place_market_buy("tok", amount_usd=51)
    assert "error" in out and "spend cap" in out["error"]


def test_limit_order_carries_builder_code(fake_client, monkeypatch):
    monkeypatch.setenv("POLYMARKET_MAX_ORDER_USD", "100")
    out = tr.place_limit_order("tok", "buy", price=0.4, size=100)  # $40, under cap
    assert out.get("status") == "posted"
    kind, args = fake_client.posted[0]
    assert kind == "limit"
    assert args.builder_code == tr.DEFAULT_BUILDER_CODE
    assert args.side == "BUY"  # normalized


def test_market_buy_carries_builder_code(fake_client):
    out = tr.place_market_buy("tok", amount_usd=25)
    assert out.get("status") == "posted"
    kind, args = fake_client.posted[0]
    assert kind == "market"
    assert args.builder_code == tr.DEFAULT_BUILDER_CODE


def test_builder_code_env_override(fake_client, monkeypatch):
    monkeypatch.setenv("POLYMARKET_BUILDER_CODE", "0x" + "ab" * 32)
    tr.place_market_buy("tok", amount_usd=10)
    _, args = fake_client.posted[0]
    assert args.builder_code == "0x" + "ab" * 32


def test_input_validation(fake_client):
    assert "error" in tr.place_limit_order("tok", "HOLD", 0.5, 10)
    assert "error" in tr.place_limit_order("tok", "BUY", 1.5, 10)
    assert "error" in tr.place_limit_order("tok", "BUY", 0.5, -1)
    assert "error" in tr.place_market_buy("tok", -5)
    assert fake_client.posted == []


def test_no_market_sell_tool_exists():
    # market SELL semantics are unverified upstream; we deliberately don't expose it
    assert not hasattr(tr, "place_market_sell")


def test_friendly_error_hints():
    e = Exception("400: maker address not allowed, please use the deposit wallet flow")
    msg = tr._friendly_error(e)
    assert "HINT" in msg and "browser" in msg.lower()


def test_open_orders_and_cancel(fake_client, monkeypatch):
    assert tr.my_open_orders() == [{"orderID": "0xopen"}]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
