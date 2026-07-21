"""Reddit collector — official OAuth API, free script-app tier.

Anonymous JSON endpoints are hard-walled (403 / interstitial, verified
2026-07); the supported route is a script app (reddit.com/prefs/apps) and
the application-only ``client_credentials`` grant, which is all a read-only
public-listing collector needs. Rate budget here is ~2 requests per
snapshot against a 100/min allowance.

Env: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
(user agent per Reddit API rules, e.g. "windows:cs-narrative-radar:v0.1
(by /u/yourname)").
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

import requests

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"


class RedditCollector:
    def __init__(self, client_id=None, client_secret=None, user_agent=None):
        self.client_id = client_id or os.environ.get("REDDIT_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("REDDIT_CLIENT_SECRET")
        self.user_agent = user_agent or os.environ.get(
            "REDDIT_USER_AGENT", "cs-narrative-radar/0.1")
        self._token = None

    def _get_token(self) -> str:
        if self._token:
            return self._token
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "Reddit credentials missing: set REDDIT_CLIENT_ID / "
                "REDDIT_CLIENT_SECRET (free script app at reddit.com/prefs/apps)")
        r = requests.post(
            TOKEN_URL, auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": self.user_agent}, timeout=20)
        r.raise_for_status()
        self._token = r.json()["access_token"]
        return self._token

    def fetch_listing(self, subreddit: str, listing: str = "hot",
                      limit: int = 100) -> list[dict]:
        r = requests.get(
            f"{API}/r/{subreddit}/{listing}",
            params={"limit": min(limit, 100), "raw_json": 1},
            headers={"Authorization": f"bearer {self._get_token()}",
                     "User-Agent": self.user_agent}, timeout=20)
        r.raise_for_status()
        return [normalize_post(c["data"])
                for c in r.json().get("data", {}).get("children", [])]

    def snapshot(self, subreddit: str, path: str,
                 listings=("hot", "top")) -> int:
        """Append one snapshot line per listing to a jsonl tape."""
        n = 0
        with open(path, "a", encoding="utf-8") as f:
            for listing in listings:
                posts = self.fetch_listing(subreddit, listing)
                f.write(json.dumps({
                    "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "subreddit": subreddit, "listing": listing,
                    "posts": posts}, ensure_ascii=False) + "\n")
                n += len(posts)
        return n


def normalize_post(d: dict) -> dict:
    """Keep only what the index needs; hash the author immediately."""
    return {
        "id": d.get("id"),
        "created_utc": d.get("created_utc"),
        "title": d.get("title") or "",
        "flair": d.get("link_flair_text"),
        "score": d.get("score", 0),
        "num_comments": d.get("num_comments", 0),
        "author": hashlib.sha1(
            str(d.get("author") or "?").encode()).hexdigest()[:16],
    }


def mentions_from_snapshots(path, lexicon, source: str = "reddit"):
    """Snapshot tape -> mention records. Weight = log1p(score) +
    log1p(comments) (heavy-tailed engagement, tamed); dedup by post id so
    re-snapshotting the same hot list doesn't double-count (latest
    engagement wins)."""
    import math

    from .attention import MentionRecord

    best: dict[tuple[str, str], MentionRecord] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            for p in snap.get("posts", []):
                if not p.get("created_utc"):
                    continue
                date = dt.datetime.fromtimestamp(
                    p["created_utc"], dt.timezone.utc).date().isoformat()
                w = math.log1p(max(p.get("score", 0), 0)) + \
                    math.log1p(max(p.get("num_comments", 0), 0))
                for m in lexicon.resolve_text(p.get("title", "")):
                    best[(p["id"], m.entity_id)] = MentionRecord(
                        date=date, source=source, entity_id=m.entity_id,
                        weight=round(max(w, 0.1), 4), author=p["author"],
                        layer="title")
    return list(best.values())
