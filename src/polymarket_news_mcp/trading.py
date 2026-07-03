"""Opt-in trading tools (v0.2.0). OFF unless the user configures their own key.

Design principles, in order:
  1. SELF-CUSTODY — the user's private key lives in THEIR environment, signs locally,
     and is never logged, echoed, or transmitted anywhere except as an order signature.
  2. OPT-IN — without POLYMARKET_PRIVATE_KEY set, these tools do not exist; the server
     stays read-only. Requires the [trading] extra: pip install polymarket-news-mcp[trading]
  3. SPEND-CAPPED — every order is bounded by POLYMARKET_MAX_ORDER_USD (default $100),
     a guardrail against agent fat-fingers. Raise it deliberately, not accidentally.
  4. ATTRIBUTED — every order carries this project's builder code (override:
     POLYMARKET_BUILDER_CODE), which is how the Polymarket Builders program attributes
     volume. It never touches user funds; fee rates are visible on the builder profile
     (currently 0 bps — users pay nothing extra).

Environment:
  POLYMARKET_PRIVATE_KEY     required to enable trading (EOA signing key, 0x-hex)
  POLYMARKET_FUNDER          optional funds address (proxy/Safe/deposit wallet)
  POLYMARKET_SIGNATURE_TYPE  optional int: 0=EOA (default without funder), 1=proxy,
                             2=browser-wallet Safe (default with funder), 3=deposit wallet
  POLYMARKET_MAX_ORDER_USD   spend cap per order (default 100)
  POLYMARKET_BUILDER_CODE    override attribution code (forks: set your own)

Known upstream caveat (July 2026): py-clob-client-v2 has open issues breaking the NEW
deposit-wallet (type 3) flow; accounts created via the website with browser wallets
(types 0/1/2) work. See README "Trading" section for links.
"""

from __future__ import annotations

import os
from typing import Optional

import httpx

# Attribution: the newsbet builder profile (public identifier, appears on-chain in the
# order's `builder` field). Fee rates on this code are 0 bps — users pay nothing extra.
DEFAULT_BUILDER_CODE = "0x103d24ddcdb487da4984bee122f89d21052631ae84c195eab32945e9dbd588b6"

CLOB_HOST = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
POLYGON = 137

_client = None  # lazy singleton


def enabled() -> bool:
    return bool(os.environ.get("POLYMARKET_PRIVATE_KEY"))


def builder_code() -> str:
    return os.environ.get("POLYMARKET_BUILDER_CODE", DEFAULT_BUILDER_CODE)


def max_order_usd() -> float:
    try:
        return float(os.environ.get("POLYMARKET_MAX_ORDER_USD", "100"))
    except ValueError:
        return 100.0


def _funder() -> Optional[str]:
    return os.environ.get("POLYMARKET_FUNDER") or None


def _signature_type() -> int:
    raw = os.environ.get("POLYMARKET_SIGNATURE_TYPE")
    if raw is not None:
        return int(raw)
    return 2 if _funder() else 0  # Safe-proxy default when a funder is given, else EOA


def get_client():
    """Two-stage auth: L1 (key) -> derive API creds -> L2 client. Lazy, cached."""
    global _client
    if _client is not None:
        return _client
    from py_clob_client_v2.client import ClobClient

    key = os.environ["POLYMARKET_PRIVATE_KEY"]
    kwargs = dict(host=CLOB_HOST, chain_id=POLYGON, key=key)
    if _funder():
        kwargs.update(funder=_funder(), signature_type=_signature_type())
    else:
        kwargs.update(signature_type=_signature_type())
    bootstrap = ClobClient(**kwargs)
    creds = bootstrap.create_or_derive_api_key()
    _client = ClobClient(**kwargs, creds=creds)
    return _client


_KNOWN_ERRORS = {
    "maker address not allowed": (
        "Your account appears to use the NEW deposit-wallet flow, which the upstream "
        "py-clob-client-v2 SDK cannot sign for yet (open issues #52/#76). Workaround: "
        "trade with an account created via the Polymarket website using a browser "
        "wallet, and set POLYMARKET_FUNDER to your Polymarket proxy address."),
    "order signer address has to be the address of the api key": (
        "Known upstream SDK limitation with deposit wallets (issue #70): the API key "
        "binds to your EOA, not the deposit wallet. Use a browser-wallet account "
        "(signature types 0/1/2) until Polymarket fixes the SDK."),
}


def _friendly_error(e: Exception) -> str:
    msg = str(e)
    for needle, hint in _KNOWN_ERRORS.items():
        if needle in msg.lower():
            return f"{msg}\n\nHINT: {hint}"
    return msg


def _cap_check(notional_usd: float) -> Optional[str]:
    cap = max_order_usd()
    if notional_usd > cap:
        return (f"order notional ${notional_usd:.2f} exceeds the spend cap "
                f"(${cap:.2f}). Raise POLYMARKET_MAX_ORDER_USD deliberately if intended.")
    return None


# --- tool implementations (registered by register_trading_tools) -------------

def trading_status() -> dict:
    """Show trading configuration: addresses, signature type, spend cap, builder code."""
    out = {
        "enabled": enabled(),
        "signature_type": _signature_type(),
        "funder": _funder(),
        "max_order_usd": max_order_usd(),
        "builder_code": builder_code(),
        "fee_note": "builder fees on this code are 0 bps — you pay nothing extra",
    }
    try:
        c = get_client()
        out["signer_address"] = getattr(c, "get_address", lambda: None)()
        out["client"] = "ok"
    except Exception as e:
        out["client"] = f"error: {_friendly_error(e)}"
    return out


def place_limit_order(token_id: str, side: str, price: float, size: float) -> dict:
    """Place a GTC limit order. side: BUY or SELL; price in $ (0-1); size in shares.
    Notional (price*size) must be within the spend cap."""
    side = side.upper()
    if side not in ("BUY", "SELL"):
        return {"error": "side must be BUY or SELL"}
    if not (0 < price < 1):
        return {"error": "price must be between 0 and 1 (exclusive)"}
    if size <= 0:
        return {"error": "size must be positive"}
    cap_err = _cap_check(price * size)
    if cap_err:
        return {"error": cap_err}
    try:
        from py_clob_client_v2.clob_types import (OrderArgs, OrderType,
                                                  PartialCreateOrderOptions)
        c = get_client()
        tick = c.get_tick_size(token_id)
        neg = c.get_neg_risk(token_id)
        args = OrderArgs(token_id=token_id, price=price, size=size, side=side,
                         builder_code=builder_code())
        resp = c.create_and_post_order(
            args, options=PartialCreateOrderOptions(tick_size=tick, neg_risk=neg),
            order_type=OrderType.GTC)
        return {"status": "posted", "response": resp,
                "attribution": "builder code attached", "tick_size": tick}
    except Exception as e:
        return {"error": _friendly_error(e)}


def place_market_buy(token_id: str, amount_usd: float) -> dict:
    """Market-BUY an outcome token, spending amount_usd (fill-or-kill).
    (Market SELL is deliberately not offered: upstream SELL-amount semantics are
    ambiguous in the v2 SDK — use place_limit_order to sell.)"""
    if amount_usd <= 0:
        return {"error": "amount_usd must be positive"}
    cap_err = _cap_check(amount_usd)
    if cap_err:
        return {"error": cap_err}
    try:
        from py_clob_client_v2.clob_types import (MarketOrderArgs, OrderType,
                                                  PartialCreateOrderOptions)
        c = get_client()
        tick = c.get_tick_size(token_id)
        neg = c.get_neg_risk(token_id)
        args = MarketOrderArgs(token_id=token_id, amount=amount_usd, side="BUY",
                               builder_code=builder_code())
        resp = c.create_and_post_market_order(
            args, options=PartialCreateOrderOptions(tick_size=tick, neg_risk=neg),
            order_type=OrderType.FOK)
        return {"status": "posted", "response": resp,
                "attribution": "builder code attached"}
    except Exception as e:
        return {"error": _friendly_error(e)}


def my_open_orders() -> list:
    """List your open CLOB orders."""
    try:
        return get_client().get_open_orders(only_first_page=True) or []
    except Exception as e:
        return [{"error": _friendly_error(e)}]


def cancel_order(order_id: str) -> dict:
    """Cancel one open order by its orderID."""
    try:
        from py_clob_client_v2.clob_types import OrderPayload
        return {"response": get_client().cancel_order(OrderPayload(orderID=order_id))}
    except Exception as e:
        return {"error": _friendly_error(e)}


def my_positions(user_address: Optional[str] = None) -> list:
    """Current positions (via the public Data API). Defaults to your funder/signer."""
    addr = user_address or _funder()
    if addr is None:
        try:
            addr = get_client().get_address()
        except Exception as e:
            return [{"error": _friendly_error(e)}]
    try:
        r = httpx.get(f"{DATA_API}/positions", params={"user": addr, "closed": "false"},
                      timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return [{"error": str(e)}]


def register_trading_tools(mcp) -> None:
    """Attach trading tools to the FastMCP server (call only when enabled())."""
    mcp.tool()(trading_status)
    mcp.tool()(place_limit_order)
    mcp.tool()(place_market_buy)
    mcp.tool()(my_open_orders)
    mcp.tool()(cancel_order)
    mcp.tool()(my_positions)
