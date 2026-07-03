"""Headline -> market matching: IDF-weighted token overlap with bigram bonus.

Deliberately not an LLM: deterministic, free, explainable (every match reports which
terms fired). An optional LLM rerank can be layered later as a premium feature. The
scoring must be read alongside the eval harness (cli: `newsbet eval`), which runs it
against live headlines + live markets and reports precision-style quality.

Score(headline, market) =
    sum(IDF(t) for shared unigrams t)            # rare shared words dominate
  + BIGRAM_BONUS * |shared bigrams|              # phrase agreement ("rate cut")
  * small liquidity tilt                         # prefer markets people can act on
Matches then collapse to one best market per event (a World Cup headline should
surface the one relevant country market, not sixty siblings)."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .models import Headline, Market, Match, token_seq, tokens

BIGRAM_BONUS = 3.0
MIN_SHARED_TERMS = 2  # at least two informative terms must agree
NUMERIC_WEIGHT = 0.5  # numbers corroborate, they don't identify
SALIENT_DF_FRACTION = 15  # salient = appears in <= max(3, n/15) markets (scales w/ corpus)
DEFAULT_THRESHOLD = 10.0  # tuned on the live eval harness (see docs/DEMO.md)
RELATIVE_CUTOFF = 0.6  # secondary matches under 60% of the top score are dropped

# rare-in-corpus but semantically weak words: they may score, but cannot be the
# salient anchor of a match ("Series A round" must not anchor to "2nd round" elections)
WEAK_SALIENT = frozenset(
    """part round rounds margin stake stakes talks deal deals report question value
    us usa america american global international government federal state states
    price prices moves surge surges crash drop drops""".split()
)

_NUM_SUFFIX = re.compile(r"^(\d+(?:\.\d+)?)([kmb])$")
_MULT = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
_NUMERIC = re.compile(r"^\d+(?:\.\d+)?$")


def norm_token(t: str) -> str:
    """Expand 120k -> 120000 so headline shorthand meets market questions."""
    m = _NUM_SUFFIX.match(t)
    if m:
        return str(int(float(m.group(1)) * _MULT[m.group(2)]))
    return t


def is_numeric(t: str) -> bool:
    return bool(_NUMERIC.match(t))


def terms(text: str) -> list[str]:
    return [norm_token(t) for t in tokens(text)]


def bigrams(text: str) -> set[str]:
    """Bigrams over the stopword-filtered NON-deduped sequence: 'bitcoin price' yes,
    'at the' never, and no fabricated adjacencies from dedupe."""
    ws = [norm_token(w) for w in token_seq(text)]
    return {f"{a} {b}" for a, b in zip(ws, ws[1:])}


@dataclass
class MarketIndex:
    """Inverted index over markets with corpus IDF weights."""

    markets: list[Market]
    idf: dict = field(default_factory=dict)
    df: dict = field(default_factory=dict)  # document frequency per term
    salient_df_max: int = 3  # df at or below this can anchor a match
    by_token: dict = field(default_factory=dict)
    _bigrams: list = field(default_factory=list)  # per-market bigram sets

    @classmethod
    def build(cls, markets: list[Market]) -> "MarketIndex":
        from .markets import is_expired  # defensive: stale caches may hold expired markets

        idx = cls(markets=[m for m in markets if not is_expired(m)])
        df: dict[str, int] = defaultdict(int)
        idx.by_token = defaultdict(list)
        n = max(len(idx.markets), 1)
        per_market_terms: list[list[str]] = []
        for i, m in enumerate(idx.markets):
            ts = terms(m.match_text())
            per_market_terms.append(ts)
            for t in set(ts):
                df[t] += 1
                idx.by_token[t].append(i)
            idx._bigrams.append(bigrams(m.match_text()))
        idx.idf = {t: math.log(n / (1 + d)) for t, d in df.items()}
        idx.df = dict(df)
        idx.salient_df_max = max(3, n // SALIENT_DF_FRACTION)
        return idx

    def match(
        self,
        headline: Headline,
        top_k: int = 3,
        threshold: float = DEFAULT_THRESHOLD,
        min_terms: int = MIN_SHARED_TERMS,
    ) -> list[Match]:
        h_terms = set(terms(headline.text()))
        if not h_terms:
            return []
        h_bigrams = bigrams(headline.text())

        # accumulate unigram scores over candidate markets sharing any term
        scores: dict[int, float] = defaultdict(float)
        shared: dict[int, list[str]] = defaultdict(list)
        salient_hits: dict[int, int] = defaultdict(int)
        for t in h_terms:
            w = self.idf.get(t, 0.0)
            if w <= 0:
                continue
            numeric = is_numeric(t)
            for i in self.by_token.get(t, ()):  # markets containing t
                scores[i] += w * (NUMERIC_WEIGHT if numeric else 1.0)
                shared[i].append(t)
                if (not numeric and t not in WEAK_SALIENT
                        and self.df.get(t, 0) <= self.salient_df_max):
                    salient_hits[i] += 1

        out: list[Match] = []
        for i, s in scores.items():
            if len(shared[i]) < min_terms:
                continue
            if salient_hits[i] < 1:  # no rare informative term -> generic-word junk
                continue
            bg = h_bigrams & self._bigrams[i]
            s += BIGRAM_BONUS * len(bg)
            m = self.markets[i]
            s *= 1.0 + 0.05 * math.log10(1.0 + max(m.volume_24h, 0.0))
            if s < threshold:
                continue
            out.append(Match(market=m, score=round(s, 2),
                             matched_terms=sorted(shared[i]) + sorted(bg)))

        # one best market per event, then best events first
        best_per_event: dict[str, Match] = {}
        for mt in out:
            key = mt.market.event_slug or mt.market.slug
            if key not in best_per_event or mt.score > best_per_event[key].score:
                best_per_event[key] = mt
        ranked = sorted(best_per_event.values(), key=lambda x: -x.score)
        # a clearly dominant top match makes weaker siblings noise, not context
        if ranked:
            floor = ranked[0].score * RELATIVE_CUTOFF
            ranked = [m for m in ranked if m.score >= floor]
        return ranked[:top_k]


def match_headlines(
    index: MarketIndex,
    headlines: list[Headline],
    top_k: int = 3,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[tuple[Headline, list[Match]]]:
    """Convenience: match many; only headlines with at least one match are returned."""
    results = []
    for h in headlines:
        ms = index.match(h, top_k=top_k, threshold=threshold)
        if ms:
            results.append((h, ms))
    return results
