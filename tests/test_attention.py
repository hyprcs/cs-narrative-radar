"""Attention engine: normalization, abnormal-z, lifecycle, end-to-end index."""
import datetime as dt

from narrative_radar.attention import (MentionRecord, abnormal_z, build_index,
                                       daily_breadth, daily_shares,
                                       lifecycle_state, read_jsonl,
                                       write_jsonl)


def _rec(date, ent, w=1.0, source="hltv", author="a1"):
    return MentionRecord(date=date, source=source, entity_id=ent,
                         weight=w, author=author, layer="text")


def test_daily_shares_normalize_per_source_day():
    recs = [
        _rec("2026-07-01", "A", 30, "hltv"),
        _rec("2026-07-01", "B", 10, "hltv"),
        _rec("2026-07-01", "A", 1, "reddit"),
        _rec("2026-07-01", "B", 3, "reddit"),
    ]
    s = daily_shares(recs)
    # hltv: A .75 / B .25; reddit: A .25 / B .75 -> combined mean .5 each:
    # a small source cannot be drowned by a big one
    assert abs(s["A"]["2026-07-01"] - 0.5) < 1e-9
    assert abs(s["B"]["2026-07-01"] - 0.5) < 1e-9


def test_breadth_counts_unique_authors():
    recs = [_rec("2026-07-01", "A", author="x"),
            _rec("2026-07-01", "A", author="x"),
            _rec("2026-07-01", "A", author="y")]
    assert daily_breadth(recs)["A"]["2026-07-01"] == 2


def _series(vals, end="2026-07-20"):
    d0 = dt.date.fromisoformat(end)
    days = [(d0 - dt.timedelta(days=len(vals) - 1 - i)).isoformat()
            for i in range(len(vals))]
    return dict(zip(days, vals))


def test_abnormal_z_flags_spikes_not_levels():
    quiet = _series([0.01] * 29 + [0.30])            # spike after a flat month
    z_spike = abnormal_z(quiet, "2026-07-20")
    assert z_spike is not None and z_spike > 3
    steady = _series([0.30] * 30)                    # permanently popular
    z_flat = abnormal_z(steady, "2026-07-20")
    assert z_flat is not None and abs(z_flat) < 1
    assert abnormal_z({}, "2026-07-20") is None      # no baseline -> honest None


def test_lifecycle_states():
    assert lifecycle_state([0.1, 0.2, 0.1, 0.0, 3.2]) == "emerging"
    assert lifecycle_state([0.1, 0.2, 2.5, 2.8, 3.2]) == "peaking"
    assert lifecycle_state([2.5, 2.8, 3.2, 0.4, 0.2]) == "fading"
    assert lifecycle_state([0.1, 0.0, 0.3, None, 0.2]) == "dormant"
    assert lifecycle_state([None, None]) == "dormant"


def test_build_index_end_to_end(tmp_path):
    recs = []
    d0 = dt.date(2026, 6, 20)
    for k in range(30):
        day = (d0 + dt.timedelta(days=k)).isoformat()
        recs.append(_rec(day, "team:big", 20, author=f"u{k}"))
        recs.append(_rec(day, "team:quiet", 1, author=f"v{k}"))
    # final day: quiet team explodes with many distinct authors
    last = (d0 + dt.timedelta(days=29)).isoformat()
    recs += [_rec(last, "team:quiet", 5, author=f"w{i}") for i in range(20)]

    path = tmp_path / "m.jsonl"
    write_jsonl(recs, path)
    idx = build_index(read_jsonl(path))
    ents = idx["entities"]
    assert idx["as_of"] == last
    assert ents["team:quiet"]["z"] > 2
    assert ents["team:quiet"]["state"] in ("emerging", "peaking")
    assert ents["team:quiet"]["breadth"] == 21
    # shares are zero-sum: the spike mechanically crowds big out, so its z
    # goes NEGATIVE (never a false positive spike) — the caveat in the
    # module docstring, pinned here
    assert ents["team:big"]["z"] < 0
    # ranking: the abnormal one outranks the merely-popular one
    assert list(ents)[0] == "team:quiet"
