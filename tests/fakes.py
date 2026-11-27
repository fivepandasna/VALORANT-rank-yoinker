"""Fake Riot endpoints (local client HTTP+WS and pd/glz/shared) for tests."""
import asyncio
import base64
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websockets

ME = "me-0000"
FRIEND = "friend-0001"
ENEMY1 = "enemy-0002"
ENEMY2 = "enemy-0003"
CURRENT = "season-current"
OLD = "season-old"
AGENT = "agent-jett"
AGENT2 = "agent-sage"


def b64(d):
    return base64.b64encode(json.dumps(d).encode()).decode()


def presence(puuid, state="INGAME", party="p1", size=2, level=100, nested=True, **extra):
    if nested:
        private = {
            "isValid": True,
            "matchPresenceData": {"sessionLoopState": state, "queueId": "competitive"},
            "partyPresenceData": {"partyId": party, "partySize": size},
            "playerPresenceData": {"accountLevel": level},
        }
    else:
        private = {"isValid": True, "sessionLoopState": state, "partyId": party,
                   "partySize": size, "accountLevel": level}
    return {"puuid": puuid, "product": "valorant", "private": b64(private), **extra}


def mmr_doc(tier, rr, games=10, wins=6, won_tiers=(), old_won=()):
    return {"QueueSkills": {"competitive": {"SeasonalInfoBySeasonID": {
        CURRENT: {"CompetitiveTier": tier, "RankedRating": rr, "NumberOfGames": games,
                  "NumberOfWinsWithPlacements": wins,
                  "WinsByTier": {str(t): 1 for t in won_tiers} or None},
        OLD: {"CompetitiveTier": 0, "NumberOfGames": 3, "NumberOfWinsWithPlacements": 1,
              "WinsByTier": {str(t): 1 for t in old_won} or None},
    }}}}


def match_doc(match_id, subjects):
    return {
        "matchInfo": {"matchId": match_id},
        "players": [{"subject": s, "stats": {"kills": 20, "deaths": 10}} for s in subjects],
        "roundResults": [{"playerStats": [
            {"subject": s, "damage": [{"headshots": 1, "bodyshots": 3, "legshots": 0}]} for s in subjects]}] * 4,
    }


class FakeRiot:
    """One HTTP server playing the local client and the remote APIs."""

    def __init__(self, latency=0.0):
        self.latency = latency
        self.counts = {}
        self.lock = threading.Lock()
        self.token_gen = 1
        self.valid_token = "tok-1"
        self.expire_next = False   # next remote call answers 400 BAD_CLAIMS
        self.rate_limit_once = set()  # path regexes answered with 429 once
        self.presences = []
        self.mmr = {}
        self.updates = {}
        self.matches = {}
        self.names = {}
        self.coregame = None
        self.pregame = None
        self.content = {"Seasons": [
            {"ID": "ep1", "Name": "EPISODE 5", "Type": "episode", "IsActive": False},
            {"ID": OLD, "Name": "ACT II", "Type": "act", "IsActive": False},
            {"ID": CURRENT, "Name": "ACT III", "Type": "act", "IsActive": True},
        ]}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, headers=None):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method):
                path = self.path
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                with fake.lock:
                    fake.counts[path.split("?")[0]] = fake.counts.get(path.split("?")[0], 0) + 1
                    key = re.sub(r"/[0-9a-z-]{4,}$", "/<id>", path.split("?")[0])
                    fake.counts[key] = fake.counts.get(key, 0) + 1
                time.sleep(fake.latency)

                if path.startswith("/entitlements/v1/token"):
                    with fake.lock:
                        return self._send(200, {"accessToken": f"tok-{fake.token_gen}", "token": "ent",
                                                "subject": ME})
                if path.startswith("/chat/v4/presences"):
                    return self._send(200, {"presences": fake.presences})

                # ---- remote APIs: need the current token
                if self.headers.get("Authorization") != f"Bearer {fake.valid_token}":
                    return self._send(400, {"errorCode": "BAD_CLAIMS"})
                if fake.expire_next:
                    with fake.lock:
                        fake.expire_next = False
                        fake.token_gen += 1
                        fake.valid_token = f"tok-{fake.token_gen}"
                    return self._send(400, {"errorCode": "BAD_CLAIMS"})
                for pattern in list(fake.rate_limit_once):
                    if re.search(pattern, path):
                        fake.rate_limit_once.discard(pattern)
                        return self._send(429, {"message": "slow down"}, {"Retry-After": "0"})

                m = re.match(r"/mmr/v1/players/([^/?]+)/competitiveupdates", path)
                if m:
                    mid = fake.updates.get(m.group(1))
                    return self._send(200, {"Matches": [{"MatchID": mid}] if mid else []})
                m = re.match(r"/mmr/v1/players/([^/?]+)$", path)
                if m:
                    doc = fake.mmr.get(m.group(1))
                    return self._send(200, doc) if doc else self._send(404, {})
                m = re.match(r"/match-details/v1/matches/(.+)", path)
                if m:
                    doc = fake.matches.get(m.group(1))
                    return self._send(200, doc) if doc else self._send(404, {})
                if path.startswith("/name-service/v2/players"):
                    return self._send(200, [{"Subject": s, "GameName": fake.names.get(s, "X"), "TagLine": "T"}
                                            for s in body])
                if path.startswith("/core-game/v1/players/"):
                    return self._send(200, {"MatchID": "match-1"}) if fake.coregame else self._send(404, {})
                if path.startswith("/core-game/v1/matches/"):
                    return self._send(200, fake.coregame) if fake.coregame else self._send(404, {})
                if path.startswith("/pregame/v1/players/"):
                    return self._send(200, {"MatchID": "match-1"}) if fake.pregame else self._send(404, {})
                if path.startswith("/pregame/v1/matches/"):
                    return self._send(200, fake.pregame) if fake.pregame else self._send(404, {})
                if path.startswith("/content-service/v3/content"):
                    return self._send(200, fake.content)
                self._send(404, {})

            def do_GET(self):
                self._handle("GET")

            def do_PUT(self):
                self._handle("PUT")

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        self.port = self.http.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

        # websocket that stands in for the client's event stream
        self._ws_loop = asyncio.new_event_loop()
        self._ws_conns = []
        ready = threading.Event()

        async def handler(ws):
            self._ws_conns.append(ws)
            try:
                async for _ in ws:
                    pass
            finally:
                self._ws_conns.remove(ws)

        async def start():
            self.ws_server = await websockets.serve(handler, "127.0.0.1", 0)
            self.ws_port = self.ws_server.sockets[0].getsockname()[1]
            ready.set()

        def run():
            asyncio.set_event_loop(self._ws_loop)
            self._ws_loop.run_until_complete(start())
            self._ws_loop.run_forever()

        threading.Thread(target=run, daemon=True).start()
        ready.wait(5)

    def push_event(self, presence_dict):
        msg = json.dumps([8, "OnJsonApiEvent_chat_v4_presences",
                          {"uri": "/chat/v4/presences", "data": {"presences": [presence_dict]}}])

        async def send():
            for ws in list(self._ws_conns):
                await ws.send(msg)
        asyncio.run_coroutine_threadsafe(send(), self._ws_loop).result(5)

    def write_client_files(self, tmp_path):
        lock = tmp_path / "lockfile"
        lock.write_text(f"Riot Client:1234:{self.port}:secret:http")
        log = tmp_path / "ShooterGame.log"
        log.write_text(
            "junk\nCI server version: release-09.01-shipping-1-123\n"
            "https://pd.eu.a.pvp.net/account-xp/v1/\nhttps://glz-eu-1.eu.a.pvp.net\n")
        return str(lock), str(log)

    def make_client(self, tmp_path):
        from src.riot import RiotClient

        lock, log = self.write_client_files(tmp_path)
        fake = self

        class Client(RiotClient):
            def connect(self):
                super().connect()
                self.urls = {"pd": fake.url, "glz": fake.url, "shared": fake.url}

        return Client(lockfile_path=lock, game_log_path=log, local_scheme="http",
                      verify_remote=True, ws_port=self.ws_port)
