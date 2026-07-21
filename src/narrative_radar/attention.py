"""The attention engine: mention records -> abnormal-attention index.

Method (README design decisions 1, 2, 4):
  * per (date, source): each entity's engagement mass is normalized by the
    source-day total (event days inflate everything; shares don't)
  * per entity: combined daily share series (sum across sources) is scored
    as a robust z in log space against the entity's own rolling baseline
    (median/MAD over `baseline_days`, excluding today) — the abnormal-
    attention construction of Da, Engelberg & Gao (2011)
  * breadth = unique authors that day (brigade resistance)
  * lifecycle = a small state machine over the recent z-series (Shiller's
    epidemic framing): dormant / emerging / peaking / fading

Caveat by construction: shares are zero-sum within a source-day, so one
entity's explosion mechanically deflates everyone else's share. With many
entities the effect is negligible, but NEGATIVE z conflates "gone quiet"
with "crowded out" — v0 therefore reads only the positive tail (spikes),
which is what content timing needs.

Pure python, no third-party deps: this module is the tested, boring core.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass

MIN_SCALE = 0.05   # z denominator floor, log1p-share units


@dataclass(frozen=True)
class MentionRecord:
    date: str | None          # ISO yyyy-mm-dd (None = undated, dropped)
    source: str               # 'hltv' | 'reddit' | ...
    entity_id: str
    weight: float             # engagement mass (1 + upvotes etc.)
    author: str               # opaque hash, breadth counting only
    layer: str                # 'text' | 'link' | 'flair-link' | 'title'


def write_jsonl(records, path, mode="a"):
    with open(path, mode, encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")


def read_jsonl(path) -> list[MentionRecord]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(MentionRecord(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue     # torn line: skip, never crash the index
    return out


def daily_shares(mentions) -> dict[str, dict[str, float]]:
    """{entity_id: {date: combined share}} — mass normalized per source-day,
    then averaged across the sources active that day."""
    mass = defaultdict(float)                 # (date, source, entity) -> mass
    totals = defaultdict(float)               # (date, source) -> mass
    for m in mentions:
        if not m.date:
            continue
        mass[(m.date, m.source, m.entity_id)] += m.weight
        totals[(m.date, m.source)] += m.weight
    per_ds = defaultdict(dict)                # (date, entity) -> {source: share}
    for (date, source, ent), v in mass.items():
        tot = totals[(date, source)]
        if tot > 0:
            per_ds[(date, ent)][source] = v / tot
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for (date, ent), shares in per_ds.items():
        out[ent][date] = sum(shares.values()) / len(shares)
    return dict(out)


def daily_breadth(mentions) -> dict[str, dict[str, int]]:
    """{entity_id: {date: unique authors}} across all sources."""
    seen = defaultdict(set)
    for m in mentions:
        if m.date:
            seen[(m.entity_id, m.date)].add(m.author)
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for (ent, date), authors in seen.items():
        out[ent][date] = len(authors)
    return dict(out)


def abnormal_z(series: dict[str, float], date: str,
               baseline_days: int = 28, min_base: int = 5) -> float | None:
    """Robust log-space z of `date`'s value vs the entity's own trailing
    baseline (median/MAD, today excluded). None when history is too thin —
    an honest 'no baseline yet', never a fake 0."""
    import datetime as dt

    d0 = dt.date.fromisoformat(date)
    base = []
    for k in range(1, baseline_days + 1):
        v = series.get((d0 - dt.timedelta(days=k)).isoformat())
        base.append(math.log1p(v) if v is not None else 0.0)
    nonzero_hist = sum(1 for b in base if b > 0)
    if len(base) < min_base or nonzero_hist == 0:
        return None
    x = math.log1p(series.get(date, 0.0))
    med = statistics.median(base)
    mad = statistics.median(abs(b - med) for b in base)
    # MIN_SCALE floors the denominator in log-share units: an entity with a
    # perfectly flat history must not turn a small wobble into z=1e9.
    scale = max(1.4826 * mad, statistics.pstdev(base), MIN_SCALE)
    return (x - med) / scale


def lifecycle_state(zs: list[float | None]) -> str:
    """State from the last few z values (oldest -> newest).
    dormant: nothing notable. emerging: latest hot, was quiet.
    peaking: hot for 2+ days. fading: was hot, cooling now."""
    known = [z for z in zs if z is not None]
    if not known:
        return "dormant"
    latest = known[-1]
    prior = known[:-1]
    hot = 2.0
    prior_hot = any(z >= hot for z in prior[-3:])
    if latest >= hot and prior_hot:
        return "peaking"
    if latest >= hot:
        return "emerging"
    if prior_hot and latest < 1.0:
        return "fading"
    return "dormant"


def build_index(mentions, baseline_days: int = 28,
                as_of: str | None = None) -> dict:
    """Per-entity summary as of the latest (or given) date."""
    shares = daily_shares(mentions)
    breadth = daily_breadth(mentions)
    all_dates = sorted({d for s in shares.values() for d in s})
    if not all_dates:
        return {"as_of": as_of, "entities": {}}
    as_of = as_of or all_dates[-1]

    import datetime as dt
    d0 = dt.date.fromisoformat(as_of)
    window = [(d0 - dt.timedelta(days=k)).isoformat() for k in range(4, -1, -1)]

    entities = {}
    for ent, series in shares.items():
        zs = [abnormal_z(series, d, baseline_days) for d in window]
        entities[ent] = {
            "share": round(series.get(as_of, 0.0), 6),
            "breadth": breadth.get(ent, {}).get(as_of, 0),
            "z": None if zs[-1] is None else round(zs[-1], 2),
            "state": lifecycle_state(zs),
            "days_seen": len(series),
        }
    ranked = sorted(entities.items(),
                    key=lambda kv: (kv[1]["z"] is not None, kv[1]["z"] or 0.0,
                                    kv[1]["share"]), reverse=True)
    return {"as_of": as_of, "baseline_days": baseline_days,
            "entities": dict(ranked)}
