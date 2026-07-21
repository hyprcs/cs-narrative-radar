"""radar -- CLI over the collectors and the attention engine."""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from .attention import build_index, read_jsonl, write_jsonl
from .entities import EntityLexicon


def _cmd_parse_hltv(args) -> int:
    from .hltv_comments import mentions_from_thread, parse_match_comments

    lex = EntityLexicon.from_csv(args.entities)
    files = sorted(glob.glob(os.path.join(args.dir, "*.html")))
    n_threads = n_posts = n_mentions = 0
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                thread = parse_match_comments(f.read())
        except OSError:
            continue
        if thread is None or not thread.posts:
            continue
        recs = mentions_from_thread(thread, lex)
        write_jsonl(recs, args.out)
        n_threads += 1
        n_posts += thread.n_posts
        n_mentions += len(recs)
    print(f"parsed {n_threads} threads / {n_posts} posts from "
          f"{len(files)} pages -> {n_mentions} mentions ({args.out})")
    return 0


def _cmd_reddit_snapshot(args) -> int:
    from .reddit import RedditCollector

    n = RedditCollector().snapshot(args.subreddit, args.out)
    print(f"snapshot: {n} posts appended -> {args.out}")
    return 0


def _cmd_reddit_mentions(args) -> int:
    from .reddit import mentions_from_snapshots

    lex = EntityLexicon.from_csv(args.entities)
    recs = mentions_from_snapshots(args.snapshot, lex)
    write_jsonl(recs, args.out)
    print(f"{len(recs)} mentions -> {args.out}")
    return 0


def _cmd_index(args) -> int:
    idx = build_index(read_jsonl(args.mentions), baseline_days=args.baseline_days,
                      as_of=args.as_of)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=1, ensure_ascii=False)
    print(f"index as of {idx['as_of']}: {len(idx['entities'])} entities -> {args.out}")
    return 0


def _cmd_brief(args) -> int:
    with open(args.index, encoding="utf-8") as f:
        idx = json.load(f)
    ents = list(idx["entities"].items())
    print(f"# narrative brief — as of {idx['as_of']}\n")
    print("## hottest (abnormal attention)")
    for ent, row in ents[:args.top]:
        z = "-" if row["z"] is None else f"{row['z']:+.1f}"
        print(f"  {ent:24s} z={z:>6}  share={row['share']:.3f}  "
              f"breadth={row['breadth']:>4}  [{row['state']}]")
    rising = [(e, r) for e, r in ents if r["state"] in ("emerging", "peaking")]
    if rising:
        print("\n## in-play narratives (emerging/peaking)")
        for ent, row in rising[:args.top]:
            print(f"  {ent:24s} [{row['state']}] z={row['z']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="radar", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("parse-hltv", help="mine saved HLTV match pages")
    p.add_argument("--dir", required=True)
    p.add_argument("--entities", required=True)
    p.add_argument("--out", default="mentions.jsonl")
    p.set_defaults(fn=_cmd_parse_hltv)

    p = sub.add_parser("reddit-snapshot", help="append one OAuth listing snapshot")
    p.add_argument("--subreddit", default="GlobalOffensive")
    p.add_argument("--out", default="reddit.jsonl")
    p.set_defaults(fn=_cmd_reddit_snapshot)

    p = sub.add_parser("reddit-mentions", help="snapshots -> mention records")
    p.add_argument("--snapshot", required=True)
    p.add_argument("--entities", required=True)
    p.add_argument("--out", default="mentions.jsonl")
    p.set_defaults(fn=_cmd_reddit_mentions)

    p = sub.add_parser("index", help="mention records -> attention index")
    p.add_argument("--mentions", required=True)
    p.add_argument("--out", default="index.json")
    p.add_argument("--baseline-days", type=int, default=28)
    p.add_argument("--as-of")
    p.set_defaults(fn=_cmd_index)

    p = sub.add_parser("brief", help="print the narrative brief")
    p.add_argument("--index", required=True)
    p.add_argument("--top", type=int, default=10)
    p.set_defaults(fn=_cmd_brief)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
