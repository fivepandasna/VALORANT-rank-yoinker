"""Backend: watches the local Riot client and pushes the match board to the GUI.

Flow: connect to the Riot client -> subscribe to presence events over its
websocket -> whenever the game state changes (menus / agent select / in game)
build a fresh board and broadcast it on ws://127.0.0.1:1100.
"""
import asyncio
import logging
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import websockets

from src.content import GameContent
from src.log import setup_logging
from src.presence import own_private, parse_event, state_key
from src.riot import RiotClient, RiotError, ValorantNotRunning
from src.server import Server
from src.tracker import EMPTY_HEARTBEAT, Token, Tracker

log = logging.getLogger("vry.main")

SUBSCRIBE_PRESENCES = '[5, "OnJsonApiEvent_chat_v4_presences"]'
RETRY_SECONDS = 5
MENU_DEBOUNCE = 1.0  # let party members' presences settle before redrawing


class App:
    def __init__(self, server=None, riot_factory=RiotClient, ws_scheme="wss"):
        self.server = server or Server()
        self.riot_factory = riot_factory
        self.ws_scheme = ws_scheme  # tests use "ws"
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        setup_logging()
        try:
            self.server.start()
        except OSError:
            log.exception("could not start the GUI server (is the port already in use?)")
            return
        last_wait = None
        while not self._stop.is_set():
            try:
                self._session()
            except ValorantNotRunning as e:
                if str(e) != last_wait:
                    log.info("waiting for VALORANT: %s", e)
                    last_wait = str(e)
            except Exception:
                log.exception("session ended unexpectedly")
            # Clear the board so the GUI shows "awaiting match".
            self.server.send_payload("heartbeat", EMPTY_HEARTBEAT)
            self._stop.wait(RETRY_SECONDS)

    # ------------------------------------------------------------------ session
    def _session(self):
        riot = self.riot_factory()
        riot.connect()
        content = GameContent(riot)
        content.load()
        tracker = Tracker(riot, content, lambda hb: self.server.send_payload("heartbeat", hb))
        try:
            asyncio.run(self._listen(riot, tracker))
        finally:
            tracker.close()

    async def _listen(self, riot, tracker):
        loop = asyncio.get_running_loop()
        refresher = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vry-refresh")
        current_key = None
        token = None

        def run_refresh(game_state, tok, delay):
            if delay:
                time.sleep(delay)
            if not tok.cancelled:
                tracker.refresh(game_state, tok)

        def schedule(key):
            nonlocal current_key, token
            if token:
                token.cancelled = True  # a newer state supersedes the running refresh
            token, current_key = Token(), key
            delay = MENU_DEBOUNCE if key[0] == "MENUS" else 0
            refresher.submit(run_refresh, key[0], token, delay)

        ssl_ctx = None
        if self.ws_scheme == "wss":
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE  # localhost, self-signed

        try:
            async with websockets.connect(
                f"{self.ws_scheme}://127.0.0.1:{riot.ws_port}",
                ssl=ssl_ctx,
                extra_headers=riot.local_auth_headers(),
            ) as ws:
                await ws.send(SUBSCRIBE_PRESENCES)
                key = await loop.run_in_executor(None, self._initial_key, riot)
                if key:
                    schedule(key)
                async for raw in ws:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "replace")
                    key = parse_event(raw, riot.puuid)
                    if key and key != current_key:
                        log.info("game state: %s", key[0])
                        schedule(key)
        finally:
            if token:
                token.cancelled = True
            refresher.shutdown(wait=False, cancel_futures=True)

    def _initial_key(self, riot, timeout=120):
        """State at startup, from the REST presences (the websocket only
        reports changes)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._stop.is_set():
            try:
                key = state_key(own_private(riot.presences(), riot.puuid))
                if key:
                    return key
            except RiotError:
                pass
            time.sleep(2)
        return None


def run():
    App().run()


if __name__ == "__main__":
    run()
