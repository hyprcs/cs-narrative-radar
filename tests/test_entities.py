"""Entity resolution: boundaries, ambiguity gate, link layer."""
from narrative_radar.entities import Entity, EntityLexicon

LEX = EntityLexicon([
    Entity("team:spirit", "team", "Team Spirit", ("Spirit",), ambiguous=True),
    Entity("team:vitality", "team", "Vitality", ("Team Vitality",)),
    Entity("team:nip", "team", "Ninjas in Pyjamas", ("NiP",), ambiguous=True),
    Entity("player:zywoo", "player", "ZywOo", (), hltv_id=11893),
    Entity("player:hate", "player", "hate", (), ambiguous=True, hltv_id=16163),
])


def _ids(text):
    return {m.entity_id for m in LEX.resolve_text(text)}


def test_unambiguous_is_case_insensitive_with_boundaries():
    assert _ids("VITALITY choke again") == {"team:vitality"}
    assert _ids("zywoo diff") == {"player:zywoo"}
    assert _ids("vitalityx") == set()          # word boundary


def test_ambiguous_requires_exact_case():
    assert _ids("Spirit are winning this") == {"team:spirit"}
    assert _ids("the spirit of the game") == set()
    assert _ids("NiP era") == {"team:nip"}
    assert _ids("nip it in the bud") == set()


def test_longest_alias_wins_and_one_mention_per_entity():
    ids = LEX.resolve_text("Team Spirit vs Team Vitality, Vitality again")
    assert {m.entity_id for m in ids} == {"team:spirit", "team:vitality"}
    assert len([m for m in ids if m.entity_id == "team:vitality"]) == 1


def test_link_resolution_beats_word_collision():
    # ambiguous all-lowercase tags NEVER text-match; the href resolves by id
    assert _ids("I hate this map") == set()
    assert _ids("hate is playing well") == set()
    m = LEX.resolve_hltv_href("/player/16163/hate", layer="flair-link")
    assert m is not None and m.entity_id == "player:hate"
    assert LEX.resolve_hltv_href("/player/999999/nobody") is None


def test_csv_roundtrip(tmp_path):
    p = tmp_path / "e.csv"
    p.write_text(
        "# comment line\n"
        "entity_id,kind,canonical,aliases,ambiguous,hltv_id\n"
        "team:m80,team,M80,,0,\n"
        "player:donk,player,donk,,0,21167\n", encoding="utf-8")
    lex = EntityLexicon.from_csv(p)
    assert {m.entity_id for m in lex.resolve_text("M80 upset donk")} == \
        {"team:m80", "player:donk"}
    assert lex.resolve_hltv_href("/player/21167/donk").entity_id == "player:donk"
