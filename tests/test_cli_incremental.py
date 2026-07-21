"""CLI incremental parsing: --state skips already-parsed pages so daily
pipelines never double-count the corpus."""
import json

from narrative_radar.cli import main

PAGE = """
<div class="match-comments"><div class="forum" data-forum-thread-id="1">
<div class="post" id="r1"><div class="standard-box">
<div class="forum-topbar"><a class="replyNum" href="#r1">#1</a>
<a href="/profile/1/u" class="authorAnchor">u</a></div>
<div class="forum-middle">M80 will win</div>
<div class="forum-bottombar"><span class="time" data-unix="1752969600000">t</span>
<div class="plus-button" data-plus-count="2"></div></div>
</div></div></div></div>
"""

CSV = ("entity_id,kind,canonical,aliases,ambiguous,hltv_id\n"
       "team:m80,team,M80,,0,\n")


def _lines(p):
    return [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_state_makes_second_run_a_noop(tmp_path, capsys):
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "1001.html").write_text(PAGE, encoding="utf-8")
    ents = tmp_path / "e.csv"
    ents.write_text(CSV, encoding="utf-8")
    out = tmp_path / "m.jsonl"
    state = tmp_path / "state.json"

    argv = ["parse-hltv", "--dir", str(pages), "--entities", str(ents),
            "--out", str(out), "--state", str(state)]
    assert main(argv) == 0
    assert len(_lines(out)) == 1
    assert json.loads(state.read_text())["1001.html"] > 0

    # second run: nothing new -> tape unchanged
    assert main(argv) == 0
    assert len(_lines(out)) == 1
    assert "(1 already in state)" in capsys.readouterr().out

    # a new page appears -> only it is appended
    (pages / "1002.html").write_text(PAGE, encoding="utf-8")
    assert main(argv) == 0
    assert len(_lines(out)) == 2
