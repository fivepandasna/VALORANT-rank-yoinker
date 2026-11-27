import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import websockets

import main
import src.content as content_mod
from src.content import GameContent
from src.riot import RiotClient, RiotError, ValorantNotRunning
from src.server import Server
from src.tracker import Token, Tracker
from tests.fakes import (AGENT, AGENT2, ENEMY1, ENEMY2, FRIEND, ME, FakeRiot, match_doc,
                         mmr_doc, presence)


@pytest.fixture
def fake():
    return FakeRiot()


def populate(fake, state="INGAME"):
    fake.names = {ME: "Me", FRIEND: "Friend", ENEMY1: "Enemy1", ENEMY2: "Enemy2"}
    fake.mmr = {
        ME: mmr_doc(14, 37, won_tiers=[14]), FRIEND: mmr_doc(20, 5, won_tiers=[20]),
        ENEMY1: mmr_doc(3, 0), ENEMY2: mmr_doc(26, 80, won_tiers=[26]),
    }
    for p in (ME, FRIEND, ENEMY1, ENEMY2):
        fake.updates[p] = "last-match"
    fake.matches["last-match"] = match_doc("last-match", [ME, FRIEND, ENEMY1, ENEMY2])
    ident = lambda lvl, inc=False, hide=False: {"AccountLevel": lvl, "Incognito": inc, "HideAccountLevel": hide}
    fake.coregame = {"MapID": "/Game/Maps/Ascent", "Players": [
        {"Subject": ME, "TeamID": "Blue", "CharacterID": AGENT.upper(), "PlayerIdentity": ident(100)},
        {"Subject": FRIEND, "TeamID": "Blue", "CharacterID": AGENT2, "PlayerIdentity": ident(250)},
        {"Subject": ENEMY1, "TeamID": "Red", "CharacterID": AGENT2, "PlayerIdentity": ident(40, inc=True)},
        {"Subject": ENEMY2, "TeamID": "Red", "CharacterID": "", "PlayerIdentity": ident(300, inc=True, hide=True)},
    ]}
    fake.pregame = {"MapID": "/Game/Maps/Ascent", "AllyTeam": {"Players": [
        {"Subject": ME, "CharacterID": AGENT, "PlayerIdentity": ident(100)},
        {"Subject": FRIEND, "CharacterID": "", "PlayerIdentity": ident(250)},
    ]}}
    fake.presences = [presence(ME, state, "p1", 2, 100), presence(FRIEND, state, "p1", 2, 250, nested=False),
                      presence(ENEMY1, state, "p9", 1), presence(ENEMY2, state, "p8", 1)]


def make_content(riot, tmp_path):
    c = GameContent(riot, cache_dir=str(tmp_path / "cache"))
    c.agents = {AGENT: {"name": "Jett", "icon": "http://icon/jett"},
                AGENT2: {"name": "Sage", "icon": "http://icon/sage"}}
    c.maps = {"/game/maps/ascent": "Ascent"}
    c.load = lambda: None
    c._load_seasons()
    return c


# ----------------------------------------------------------------- RiotClient
def test_connect_parses_lockfile_log_and_token(fake, tmp_path):
    riot = fake.make_client(tmp_path)
    riot.connect()
    assert riot.puuid == ME and riot.headers["Authorization"] == "Bearer tok-1"
    assert riot.headers["X-Riot-ClientVersion"] == "release-09.01-shipping-1-123"
    assert riot.port == str(fake.port)


def test_connect_without_lockfile_raises_cleanly(tmp_path):
    with pytest.raises(ValorantNotRunning):
        RiotClient(lockfile_path=str(tmp_path / "nope"), game_log_path=str(tmp_path / "nope2")).connect()


def test_log_scan_gives_up_instead_of_hanging(tmp_path):
    (tmp_path / "lock").write_text("a:1:2:pw:https")
    (tmp_path / "log").write_text("no region info here\n")
    riot = RiotClient(lockfile_path=str(tmp_path / "lock"), game_log_path=str(tmp_path / "log"))
    riot._read_lockfile()
    t = time.monotonic()
    with pytest.raises(ValorantNotRunning):
        riot._scan_game_log(timeout=1)
    assert time.monotonic() - t < 3  # the old code looped forever here


def test_429_is_retried(fake, tmp_path):
    populate(fake)
    riot = fake.make_client(tmp_path); riot.connect()
    fake.rate_limit_once.add(r"/mmr/v1/players/")
    assert riot.mmr(ME)["QueueSkills"]
    assert fake.counts["/mmr/v1/players/<id>"] == 2


def test_expired_token_refreshed_once_across_threads(fake, tmp_path):
    populate(fake)
    riot = fake.make_client(tmp_path); riot.connect()
    fake.expire_next = True
    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(riot.mmr, [ME, FRIEND, ENEMY1, ENEMY2, ME, FRIEND]))
    assert all(results)
    assert fake.counts["/entitlements/v1/token"] == 2  # connect + exactly one refresh
    assert riot.headers["Authorization"] == f"Bearer {fake.valid_token}"


def test_retries_are_bounded(fake, tmp_path, monkeypatch):
    riot = fake.make_client(tmp_path); riot.connect()
    fake.valid_token = "something-else-forever"
    monkeypatch.setattr("src.riot.time.sleep", lambda s: None)
    # token refresh keeps handing out a token the server rejects -> must give up, not recurse forever
    t = time.monotonic()
    with pytest.raises(RiotError):
        riot.mmr(ME)
    assert time.monotonic() - t < 5


# -------------------------------------------------------------------- Tracker
def run_build(fake, tmp_path, state):
    riot = fake.make_client(tmp_path); riot.connect()
    tracker = Tracker(riot, make_content(riot, tmp_path), lambda hb: None)
    return tracker, tracker.build(state, Token())


def test_ingame_board_matches_what_the_gui_reads(fake, tmp_path):
    populate(fake)
    _, hb = run_build(fake, tmp_path, "INGAME")
    assert hb["state"] == "INGAME" and hb["puuid"] == ME and hb["map"] == "Ascent"
    pl = hb["players"]
    me = pl[ME]
    assert me == {
        "name": "Me#T", "team": "Blue", "agent": "Jett", "agentImgLink": "http://icon/jett",
        "rank": 14, "rr": 37, "peakRank": 14, "peakRankAct": "(e5a3)", "winPercentage": "60 (10)",
        "kd": 2.0, "headshotPercentage": 25, "level": 100, "partyNumber": 1,
    }
    assert pl[FRIEND]["partyNumber"] == 1 and pl[FRIEND]["name"] == "Friend#T"  # own party: never hidden
    assert pl[ENEMY1]["partyNumber"] == 0 and pl[ENEMY1]["team"] == "Red"
    # sorted by displayed level, highest first, hidden level last
    assert list(pl) == [FRIEND, ME, ENEMY1, ENEMY2]
    # fields the GUI reads must exist on every player
    keys = {"name", "team", "agent", "agentImgLink", "rank", "rr", "peakRank", "peakRankAct",
            "winPercentage", "kd", "headshotPercentage", "level", "partyNumber"}
    assert all(set(p) == keys for p in pl.values())
    json.dumps(hb)  # serialisable


def test_streamer_mode_is_respected(fake, tmp_path):
    populate(fake)
    _, hb = run_build(fake, tmp_path, "INGAME")
    pl = hb["players"]
    assert pl[ENEMY1]["name"] == "Sage"            # incognito -> agent name, not the real name
    assert pl[ENEMY2]["name"] == "Player"          # incognito, no agent picked
    assert pl[ENEMY2]["level"] is None             # hidden level not leaked
    assert pl[ENEMY1]["level"] == 40               # visible level kept
    assert "Enemy1" not in json.dumps(hb) and "Enemy2" not in json.dumps(hb)


def test_pregame_and_menus_boards(fake, tmp_path):
    populate(fake, "PREGAME")
    _, hb = run_build(fake, tmp_path, "PREGAME")
    assert hb["state"] == "PREGAME" and hb["map"] == "Ascent"
    assert hb["players"][ME]["agentImgLink"] == "http://icon/jett" and hb["players"][ME]["team"] is None
    assert hb["players"][FRIEND]["agent"] == ""   # not picked yet
    populate(fake, "MENUS")
    _, hb = run_build(fake, tmp_path, "MENUS")
    assert set(hb["players"]) == {ME, FRIEND} and hb["map"] is None
    assert hb["players"][FRIEND]["level"] == 250 and hb["players"][ME]["partyNumber"] == 0


def test_missing_data_degrades_to_defaults(fake, tmp_path):
    populate(fake)
    fake.mmr.pop(ENEMY1)   # 404
    fake.updates.pop(ENEMY2)  # no recent comp match
    _, hb = run_build(fake, tmp_path, "INGAME")
    e1, e2 = hb["players"][ENEMY1], hb["players"][ENEMY2]
    assert (e1["rank"], e1["winPercentage"]) == (0, "N/A (0)")
    assert e2["kd"] == "N/A" and e2["headshotPercentage"] == "N/A"


def test_no_match_yet_returns_none(fake, tmp_path):
    populate(fake)
    fake.coregame = None
    _, hb = run_build(fake, tmp_path, "INGAME")
    assert hb is None


def test_pregame_data_is_reused_ingame_and_match_fetched_once(fake, tmp_path):
    populate(fake)
    riot = fake.make_client(tmp_path); riot.connect()
    tracker = Tracker(riot, make_content(riot, tmp_path), lambda hb: None)
    tracker.build("PREGAME", Token())
    before = dict(fake.counts)
    tracker.build("INGAME", Token())
    # ME and FRIEND were loaded in pregame: no new mmr calls for them, only the 2 enemies
    assert fake.counts["/mmr/v1/players/<id>"] - before["/mmr/v1/players/<id>"] == 2
    # all four share one last match -> one match-details download total
    assert fake.counts["/match-details/v1/matches/last-match"] == 1


def test_players_load_in_parallel(tmp_path):
    fake = FakeRiot(latency=0.15)
    populate(fake)
    riot = fake.make_client(tmp_path); riot.connect()
    tracker = Tracker(riot, make_content(riot, tmp_path), lambda hb: None)
    t = time.monotonic()
    tracker.build("INGAME", Token())
    elapsed = time.monotonic() - t
    # sequential would be 4 players x 3 calls x 0.15s = 1.8s (+names, presences)
    print(f"\n4-player board with 150ms latency: {elapsed:.2f}s")
    assert elapsed < 1.0


# ------------------------------------------------------------------ end to end
def test_full_app_pipeline(fake, tmp_path, monkeypatch):
    populate(fake)
    monkeypatch.setattr(content_mod, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(main, "MENU_DEBOUNCE", 0.05)
    # no internet in tests: pre-seed the valorant-api cache
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "agents.json").write_text(json.dumps({"data": [
        {"uuid": AGENT, "displayName": "Jett", "displayIcon": "http://icon/jett"},
        {"uuid": AGENT2, "displayName": "Sage", "displayIcon": "http://icon/sage"}]}))
    (tmp_path / "cache" / "maps.json").write_text(json.dumps({"data": [
        {"mapUrl": "/Game/Maps/Ascent", "displayName": "Ascent"}]}))

    server = Server(port=0)
    app = main.App(server=server, riot_factory=lambda: fake.make_client(tmp_path), ws_scheme="ws")
    monkeypatch.setattr(main, "RETRY_SECONDS", 0.2)
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while server._server is None and time.time() < deadline:
        time.sleep(0.02)

    async def gui():
        got = []
        async with websockets.connect(f"ws://127.0.0.1:{server.port}") as ws:
            async def next_heartbeat(pred, timeout=10):
                end = time.time() + timeout
                while time.time() < end:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
                    got.append(msg)
                    if msg.get("type") == "heartbeat" and pred(msg):
                        return msg
                raise AssertionError(f"no matching heartbeat; saw {[m.get('state') for m in got]}")

            # 1. initial state comes from the REST presences: INGAME
            hb = await next_heartbeat(lambda m: m["state"] == "INGAME")
            assert len(hb["players"]) == 4 and hb["players"][ME]["rank"] == 14
            # 2. game ends -> the websocket reports MENUS -> board switches to the party
            fake.presences = [presence(ME, "MENUS", "p1", 2, 100), presence(FRIEND, "MENUS", "p1", 2, 250)]
            fake.push_event(presence(ME, "MENUS", "p1", 2, 100))
            hb = await next_heartbeat(lambda m: m["state"] == "MENUS")
            assert set(hb["players"]) == {ME, FRIEND}
            # 3. a late-joining GUI is replayed the latest board
            async with websockets.connect(f"ws://127.0.0.1:{server.port}") as late:
                replay = json.loads(await asyncio.wait_for(late.recv(), 5))
                assert replay["state"] == "MENUS" and replay["type"] == "heartbeat"
            # 4. an unrelated presence event (a friend) does not trigger a refresh
            calls = fake.counts.get("/mmr/v1/players/<id>", 0)
            fake.push_event(presence("randomfriend", "INGAME"))
            await asyncio.sleep(0.5)
            assert fake.counts.get("/mmr/v1/players/<id>", 0) == calls

    asyncio.run(gui())
    app.stop()
