from src.content import parse_seasons, season_number
from src.presence import decode_private, party_groups, party_members, parse_event, state_key, own_private
from src.rank import parse_rank
from src.stats import compute_stats
from tests.fakes import CURRENT, OLD, ME, FRIEND, ENEMY1, mmr_doc, presence, match_doc, b64

LABELS = {CURRENT: (5, 3), OLD: (5, 2), "new": ("v25", 1)}


def test_rank_basic():
    r = parse_rank(mmr_doc(14, 37, games=10, wins=6, won_tiers=[12, 14]), CURRENT, LABELS)
    assert (r["rank"], r["rr"], r["peak"], r["wr"], r["games"]) == (14, 37, 14, 60, 10)
    assert r["peak_act"] == "(e5a3)"


def test_rank_peak_from_older_season_and_label():
    r = parse_rank(mmr_doc(12, 10, won_tiers=[12], old_won=[18]), CURRENT, LABELS)
    assert r["peak"] == 18 and r["peak_act"] == "(e5a2)"


def test_rank_unranked_and_missing():
    assert parse_rank(mmr_doc(0, 99), CURRENT, LABELS)["rank"] == 0
    assert parse_rank(mmr_doc(0, 99), CURRENT, LABELS)["rr"] == 0
    for bad in (None, {}, {"QueueSkills": None}, {"QueueSkills": {"competitive": {"SeasonalInfoBySeasonID": None}}}):
        r = parse_rank(bad, CURRENT, LABELS)  # used to raise UnboundLocalError on bad responses
        assert (r["rank"], r["rr"], r["peak"], r["wr"], r["peak_act"]) == (0, 0, 0, "N/A", "")


def test_rank_zero_games_is_na_not_100():
    assert parse_rank(mmr_doc(5, 1, games=0, wins=0), CURRENT, LABELS)["wr"] == "N/A"


def test_rank_old_numbering_adjusted():
    from src.constants import BEFORE_ASCENDANT_SEASONS
    old = next(iter(BEFORE_ASCENDANT_SEASONS))
    doc = {"QueueSkills": {"competitive": {"SeasonalInfoBySeasonID": {
        CURRENT: {"CompetitiveTier": 10},
        old: {"WinsByTier": {"24": 3}},  # old Radiant
    }}}}
    assert parse_rank(doc, CURRENT, {})["peak"] == 27


def test_new_style_act_label():
    doc = {"QueueSkills": {"competitive": {"SeasonalInfoBySeasonID": {"new": {"CompetitiveTier": 20}}}}}
    assert parse_rank(doc, "new", LABELS)["peak_act"] == "(v25a1)"


def test_season_parsing():
    content = {"Seasons": [
        {"ID": "E1", "Name": "EPISODE 4", "Type": "episode"},
        {"ID": "A1", "Name": "ACT IV", "Type": "act", "IsActive": False},
        {"ID": "E2", "Name": "EPISODE V25", "Type": "episode"},
        {"ID": "A2", "Name": "ACT 2", "Type": "act", "IsActive": True},
    ]}
    active, labels = parse_seasons(content)
    assert active == "a2" and labels == {"a1": (4, 4), "a2": ("v25", 2)}
    assert season_number("EPISODE IX") == 9 and season_number(None) is None and season_number("x") is None


def test_stats():
    s = compute_stats(ME, match_doc("m", [ME, FRIEND]))
    assert s == {"kd": 2.0, "hs": 25}
    assert compute_stats("nobody", match_doc("m", [ME])) == {"kd": 0, "hs": "N/A"}
    assert compute_stats(ME, {})["hs"] == "N/A"


def test_presence_nested_and_flat_agree():
    for nested in (True, False):
        priv = decode_private(presence(ME, nested=nested, level=42))
        assert priv["sessionLoopState"] == "INGAME" and priv["accountLevel"] == 42 and priv["partyId"] == "p1"


def test_presence_garbage_is_ignored():
    assert decode_private({"private": "!!!notbase64"}) is None
    assert decode_private({"private": ""}) is None
    assert decode_private({"private": b64([1, 2])}) is None
    assert state_key(None) is None and state_key({}) is None


def test_state_key_and_event():
    ev = presence(ME, state="MENUS", party="pX", size=3)
    assert state_key(decode_private(ev)) == ("MENUS", ("pX", 3))
    assert state_key(decode_private(presence(ME, state="INGAME"))) == ("INGAME", None)
    import json
    raw = json.dumps([8, "e", {"uri": "/chat/v4/presences", "data": {"presences": [ev]}}])
    assert parse_event(raw, ME) == ("MENUS", ("pX", 3))
    assert parse_event(raw, "someone-else") is None
    assert parse_event("not json " + ME + "/chat/v4/presences", ME) is None


def test_lol_presence_ignored():
    lol = {"puuid": ME, "product": "league_of_legends", "private": presence(ME)["private"]}
    assert own_private([lol], ME) is None


def test_party_groups_and_members():
    pres = [presence(ME, party="p1", size=2, level=100), presence(FRIEND, party="p1", size=2, level=50, nested=False),
            presence(ENEMY1, party="solo", size=1)]
    assert party_groups(pres, [ME, FRIEND, ENEMY1]) == {"p1": [ME, FRIEND]}
    members = party_members(pres, ME)
    assert [(m["puuid"], m["level"]) for m in members] == [(ME, 100), (FRIEND, 50)]
    assert party_members([], ME) == [{"puuid": ME, "level": None}]
