# polymarket-news-mcp
<!-- mcp-name: io.github.mobymedia/polymarket-news-mcp -->

**Give an AI agent a headline — get the Polymarket markets it moves.**

An MCP (Model Context Protocol) server that connects real-world news to prediction
markets. Plenty of MCP servers can look up Polymarket prices; this one answers a
different question: *"this just happened — where can the world's belief about it be
read?"* Matching is deterministic and explainable — every match reports the exact
terms that fired and a confidence score, so your agent can judge the match itself.

Read-only by design: no API keys, no wallet, no trading. Live odds and links only.

## Tools

| Tool | What it does |
|---|---|
| `match_news(text, top_k)` | **The headline tool.** News headline/snippet → the markets it could move, with live odds, 24h volume, match score, matched terms, deep links |
| `latest_matched_news(top_k, max_age_hours)` | Live feed: recent headlines from major outlets (crypto, politics, world, sports), each matched to its markets |
| `search_markets(query, top_k)` | Lenient free-text search over ~2,000 active markets |
| `trending_markets(top_k)` | Most active markets right now by 24h volume |
| `get_market(slug_or_url)` | One market's live odds by URL or slug |

### Example

> `match_news("Federal Reserve signals rate hike as inflation stays hot")`

```json
[
  {"question": "Fed rate hike in 2026?", "match_score": 17.2,
   "outcomes": {"Yes": 0.47, "No": 0.53}, "volume_24h_usd": 812440,
   "matched_terms": ["fed", "hike", "rate", "rate hike"],
   "url": "https://polymarket.com/event/fed-rate-hike-in-2026"},
  {"question": "Fed Rate Hike by July 2026 Meeting?", "match_score": 17.0,
   "outcomes": {"Yes": 0.08, "No": 0.92}, ...}
]
```

## Install

Requires Python ≥3.10. With [uv](https://docs.astral.sh/uv/) (no install needed):

```bash
uvx polymarket-news-mcp
```

**Claude Code**
```bash
claude mcp add polymarket-news -- uvx polymarket-news-mcp
```

**Claude Desktop / any MCP client** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "polymarket-news": {
      "command": "uvx",
      "args": ["polymarket-news-mcp"]
    }
  }
}
```

Or classic pip: `pip install polymarket-news-mcp`
then command `polymarket-news-mcp`.


## Trading (opt-in, v0.2.0+)

By default this server is **read-only**. Trading tools appear only when you provide
your own signing key and install the extra:

```bash
pip install "polymarket-news-mcp[trading]"
export POLYMARKET_PRIVATE_KEY=0x...     # YOUR key. Stays local, signs locally.
export POLYMARKET_FUNDER=0x...          # your Polymarket proxy/funds address (browser-wallet accounts)
# optional: POLYMARKET_SIGNATURE_TYPE (0 EOA / 1 proxy / 2 Safe / 3 deposit wallet)
# optional: POLYMARKET_MAX_ORDER_USD (spend cap per order, default 100)
```

Tools: `trading_status`, `place_limit_order`, `place_market_buy`, `my_open_orders`,
`cancel_order`, `my_positions`. Every order is bounded by the spend cap.

**Transparency — builder attribution.** Orders placed through this server carry the
`newsbet` builder code (0 bps maker/taker — **you pay no extra fees**); Polymarket's
Builders program attributes routed volume to the project. Forks can override via
`POLYMARKET_BUILDER_CODE`.

**Security.** Your key never leaves your machine and is never logged. Use a dedicated
trading wallet with limited funds. Market SELL is deliberately not offered (ambiguous
upstream semantics) — sell via limit orders.

**Known upstream caveat (July 2026).** Polymarket's `py-clob-client-v2` currently cannot
sign for NEW "deposit wallet" accounts ([#52](https://github.com/Polymarket/py-clob-client-v2/issues/52),
[#70](https://github.com/Polymarket/py-clob-client-v2/issues/70),
[#76](https://github.com/Polymarket/py-clob-client-v2/issues/76)). Accounts created via
the Polymarket website with a browser wallet (signature types 0/1/2) work — set
`POLYMARKET_FUNDER` to your Polymarket profile address.

**Jurisdiction.** Polymarket's CLOB rejects orders from restricted regions (incl. the
US) server-side; using these tools where prohibited is on you. Nothing here is
financial advice.

## How matching works

IDF-weighted term overlap between your text and market questions/event titles, plus:
phrase (bigram) agreement, numeric normalization ($120,000 ≡ 120k), accent folding,
a salience gate (every match needs a rare informative anchor — "openai", never "the"),
one-best-market-per-event dedupe, and a relative cutoff. Market data comes from
Polymarket's public Gamma API (top ~2,000 markets by liquidity, cached 15 minutes at
`~/.cache/polymarket-news-mcp/`). First call after a cold start takes ~10–15s to build
the index; subsequent calls are instant.

## Notes & limits

- Informational only — not financial advice. Polymarket availability depends on your
  jurisdiction; this server never places orders.
- News feed sources: CoinDesk, Cointelegraph, Decrypt, The Block, SEC, BBC World &
  Politics, ESPN (RSS). PRs welcome for more.
- Match scores ≥15 are high-confidence; 10–15 are related-entity matches.

## License

MIT
