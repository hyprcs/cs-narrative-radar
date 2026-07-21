"""Parser for the comment thread embedded in every HLTV match page.

Anchors (verified against 10k+ saved pages, 2025-2026 markup):
  div.match-comments > div.forum[data-forum-thread-id]
    div.post[id=r<postid>]
      .forum-topbar   -> a.replyNum (#N), .fan-con span[title="fan of X"]
                         with an entity <a href="/player|/team/...">,
                         a.authorAnchor[href=/profile/<id>/<nick>]
      .forum-middle   -> post text
      .forum-bottombar-> span.time[data-unix], .plus-button[data-plus-count]
  threading: div[data-threading-reply-parent=r<parent>]

Everything is best-effort per post: one malformed post never drops the
thread. Output is plain dataclasses; no author text leaves this module in
aggregate pipelines (see README ethics note).
"""
from __future__ import annotations

import hashlib
import importlib.util
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

# lxml is ~10x faster than html.parser and the difference is not cosmetic:
# recent big-event match pages carry 1000+ comment posts and stdlib parsing
# of a 10k-page corpus runs into hours. Optional dependency (.[fast]).
_PARSER = "lxml" if importlib.util.find_spec("lxml") else "html.parser"


@dataclass
class FanTag:
    kind: str            # css class of the flair span, e.g. 'love'
    target_href: str     # /player/<id>/<name> or /team/<id>/<name>
    target_name: str


@dataclass
class Post:
    post_id: str
    reply_num: int | None
    author_hash: str     # sha1 of profile href -- never store the name
    country: str | None
    ts_unix: int | None
    plus_count: int
    text: str
    parent_post_id: str | None
    fan_tags: list[FanTag] = field(default_factory=list)


@dataclass
class MatchThread:
    thread_id: str | None
    posts: list[Post]

    @property
    def n_posts(self) -> int:
        return len(self.posts)


def _hash_author(href: str | None) -> str:
    return hashlib.sha1((href or "?").encode("utf-8")).hexdigest()[:16]


def parse_match_comments(html: str) -> MatchThread | None:
    """The match page's comment thread, or None when the block is absent."""
    soup = BeautifulSoup(html or "", _PARSER)
    box = soup.select_one(".match-comments")
    if box is None:
        return None
    forum = box.select_one(".forum")
    thread_id = forum.get("data-forum-thread-id") if forum else None

    posts: list[Post] = []
    for p in box.select("div.post"):
        try:
            pid = p.get("id") or ""
            top = p.select_one(".forum-topbar")
            reply_num = None
            author_href = None
            country = None
            fan_tags: list[FanTag] = []
            if top is not None:
                rn = top.select_one("a.replyNum")
                if rn and rn.get_text(strip=True).lstrip("#").isdigit():
                    reply_num = int(rn.get_text(strip=True).lstrip("#"))
                aa = top.select_one("a.authorAnchor")
                author_href = aa.get("href") if aa else None
                flag = top.select_one("img.flag")
                country = flag.get("title") if flag else None
                for span in top.select(".fan-con span"):
                    a = span.select_one("a[href*='/player/'], a[href*='/team/']")
                    if a is not None:
                        fan_tags.append(FanTag(
                            kind=(span.get("class") or ["?"])[0],
                            target_href=a.get("href") or "",
                            target_name=a.get_text(strip=True)))
            mid = p.select_one(".forum-middle")
            text = mid.get_text(" ", strip=True) if mid else ""
            ts = None
            t = p.select_one(".forum-bottombar span.time[data-unix]")
            if t is not None:
                try:
                    ts = int(t["data-unix"]) // 1000
                except (KeyError, ValueError, TypeError):
                    ts = None
            plus = 0
            pb = p.select_one(".plus-button[data-plus-count]")
            if pb is not None:
                try:
                    plus = int(pb["data-plus-count"])
                except (KeyError, ValueError, TypeError):
                    plus = 0
            parent = None
            thr = p.find_parent(attrs={"data-threading-reply-parent": True})
            if thr is not None:
                parent = thr.get("data-threading-reply-parent")
            posts.append(Post(
                post_id=pid, reply_num=reply_num,
                author_hash=_hash_author(author_href), country=country,
                ts_unix=ts, plus_count=plus, text=text,
                parent_post_id=parent, fan_tags=fan_tags))
        except Exception:   # noqa: BLE001 -- one bad post never drops a thread
            continue
    return MatchThread(thread_id=thread_id, posts=posts)


def mentions_from_thread(thread: MatchThread, lexicon, source: str = "hltv"):
    """Flatten a parsed thread into mention records (see attention.py).

    Weight = 1 + plus_count (an upvoted comment carries its audience with
    it). Layers: 'text' (dictionary match on the comment body) and
    'flair-link' (declared fan/hate allegiance -- id-resolved, unambiguous).
    """
    from .attention import MentionRecord   # local import: no cycle at import time

    out: list[MentionRecord] = []
    for post in thread.posts:
        date = None
        if post.ts_unix:
            import datetime as _dt
            date = _dt.datetime.fromtimestamp(
                post.ts_unix, _dt.timezone.utc).date().isoformat()
        w = 1.0 + float(post.plus_count)
        for m in lexicon.resolve_text(post.text):
            out.append(MentionRecord(date=date, source=source,
                                     entity_id=m.entity_id, weight=w,
                                     author=post.author_hash, layer="text"))
        for tag in post.fan_tags:
            m = lexicon.resolve_hltv_href(tag.target_href, layer="flair-link")
            if m is not None:
                out.append(MentionRecord(date=date, source=source,
                                         entity_id=m.entity_id, weight=1.0,
                                         author=post.author_hash,
                                         layer="flair-link"))
    return out
