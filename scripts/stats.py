#!/usr/bin/env python3
"""Adoption signals for polymarket-news-mcp. Run: python3 scripts/stats.py
Uses the keychain GitHub token for traffic stats (falls back to public data)."""
import json, subprocess, urllib.request

REPO = "mobymedia/polymarket-news-mcp"

def token():
    try:
        out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n",
                             capture_output=True, text=True, timeout=10).stdout
        return dict(l.split("=", 1) for l in out.strip().splitlines()).get("password")
    except Exception:
        return None

def get(url, tok=None):
    req = urllib.request.Request(url, headers={"User-Agent": "stats"})
    if tok:
        req.add_header("Authorization", f"token {tok}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except Exception as e:
        return {"error": str(e)}

tok = token()
repo = get(f"https://api.github.com/repos/{REPO}")
print(f"stars: {repo.get('stargazers_count', '?')}  forks: {repo.get('forks_count', '?')}  "
      f"watchers: {repo.get('subscribers_count', '?')}  issues: {repo.get('open_issues_count', '?')}")
if tok:
    cl = get(f"https://api.github.com/repos/{REPO}/traffic/clones", tok)
    vw = get(f"https://api.github.com/repos/{REPO}/traffic/views", tok)
    print(f"clones 14d: {cl.get('count', '?')} ({cl.get('uniques', '?')} unique)   "
          f"views 14d: {vw.get('count', '?')} ({vw.get('uniques', '?')} unique)")
    ref = get(f"https://api.github.com/repos/{REPO}/traffic/popular/referrers", tok)
    if isinstance(ref, list) and ref:
        print("referrers 14d:", ", ".join(f"{r['referrer']} ({r['count']} views/{r['uniques']} uniq)" for r in ref[:6]))
    else:
        print("referrers 14d: none yet")

# PyPI downloads (ingestion lags ~1 day; excludes some mirrors)
for period, label in [("recent", None)]:
    d = get("https://pypistats.org/api/packages/polymarket-news-mcp/recent")
    if "data" in d:
        dd = d["data"]
        print(f"pypi downloads: {dd.get('last_day','?')} yesterday · "
              f"{dd.get('last_week','?')} last 7d · {dd.get('last_month','?')} last 30d")
    else:
        print("pypi downloads: no data yet (stats lag ~24h after publish)")

print("\nTRIGGER BAR (set 2026-07-03): sustained 1,000+ weekly tool calls or unsolicited")
print("'can I pay for X' issues -> revisit paid tier. Below that: keep it free.")
