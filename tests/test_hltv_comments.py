"""HLTV comment-thread parser on a SYNTHETIC fixture that mirrors the real
2025-2026 markup structure (no real user content ships with this repo)."""
from narrative_radar.entities import Entity, EntityLexicon
from narrative_radar.hltv_comments import (mentions_from_thread,
                                           parse_match_comments)

PAGE = """
<html><body>
<div class="match-comments" data-original-overlay-location="/matches/1/x">
  <div class="forum no-promode" data-forum-thread-id="777001">
    <div class="post " id="r1001">
      <div class="standard-box">
        <div class="forum-topbar" data-topbar-post="r1001">
          <a href="/matches/1/x#r1001" class="replyNum">#1</a>
          <div class="fan-con"><span class="love" title="fan of hate">
            <a href="/player/16163/hate" class="a-reset">hate</a></span></div>
          <img class="flag" title="Haiti"/>
          <a href="/profile/555/somefan" class="authorAnchor">somefan</a>
        </div>
        <div class="forum-middle">ZywOo carries again, Vitality look scary</div>
        <div class="forum-bottombar">
          <span class="time" data-unix="1764378384000">2025-11-29 12:06</span>
          <div class="plus-button disable" data-plus-count="4"></div>
        </div>
      </div>
    </div>
    <div class="children">
      <div class="threading" data-threading-reply-parent="r1001">
        <div class="post " id="r1002">
          <div class="standard-box">
            <div class="forum-topbar" data-topbar-post="r1002">
              <a href="/matches/1/x#r1002" class="replyNum">#2</a>
              <a href="/profile/556/otherfan" class="authorAnchor">otherfan</a>
            </div>
            <div class="forum-middle">nah, m4 diff</div>
            <div class="forum-bottombar">
              <span class="time" data-unix="1764378484000">x</span>
              <div class="plus-button" data-plus-count="0"></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
</body></html>
"""

LEX = EntityLexicon([
    Entity("team:vitality", "team", "Vitality", ()),
    Entity("player:zywoo", "player", "ZywOo", (), hltv_id=11893),
    Entity("player:hate", "player", "hate", (), ambiguous=True, hltv_id=16163),
])


def test_parse_thread_structure():
    t = parse_match_comments(PAGE)
    assert t is not None and t.thread_id == "777001"
    assert t.n_posts == 2
    p1, p2 = t.posts
    assert p1.post_id == "r1001" and p1.reply_num == 1
    assert p1.plus_count == 4 and p1.country == "Haiti"
    assert p1.ts_unix == 1764378384
    assert p1.text.startswith("ZywOo carries")
    assert p1.parent_post_id is None
    assert p2.parent_post_id == "r1001"
    assert p1.fan_tags and p1.fan_tags[0].target_href == "/player/16163/hate"
    # author identity is hashed, never the profile name
    assert "somefan" not in p1.author_hash


def test_missing_block_is_none():
    assert parse_match_comments("<html><body>no comments</body></html>") is None
    assert parse_match_comments("") is None


def test_mentions_layers_and_weights():
    t = parse_match_comments(PAGE)
    recs = mentions_from_thread(t, LEX)
    by = {(r.entity_id, r.layer): r for r in recs}
    # text layer: ZywOo + Vitality from post 1, weight 1 + plus_count
    assert by[("player:zywoo", "text")].weight == 5.0
    assert by[("team:vitality", "text")].weight == 5.0
    # flair layer: 'hate' resolved by id despite being a gated word
    assert by[("player:hate", "flair-link")].weight == 1.0
    # the word 'hate' never produced a text mention
    assert ("player:hate", "text") not in by
    import datetime as dt
    expect = dt.datetime.fromtimestamp(
        1764378384, dt.timezone.utc).date().isoformat()
    assert {r.date for r in recs if r.author == t.posts[0].author_hash} == {expect}
