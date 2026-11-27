"""Talks to the local Riot client and Riot's remote (pd / glz / shared) APIs.

Design notes
- One pooled ``requests.Session`` per target, with timeouts on every call.
- Remote TLS is verified. Only the localhost client (self-signed) is not.
  If verification ever breaks on your machine, set ``VRY_INSECURE_TLS=1``.
- Retries are bounded. 429 honours ``Retry-After``; expired tokens are
  refreshed once (shared between threads) and the request retried.
- Safe to call from many threads; at most ``MAX_IN_FLIGHT`` requests run at once.
"""
import base64
import logging
import os
import re
import threading
import time

import requests
import urllib3
from requests.adapters import HTTPAdapter

log = logging.getLogger("vry.riot")

# Only localhost requests are unverified; silence the matching warning.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TIMEOUT = (5, 20)  # connect, read (seconds)
MAX_ATTEMPTS = 4
MAX_IN_FLIGHT = 6

CLIENT_PLATFORM = (
    "ew0KCSJwbGF0Zm9ybVR5cGUiOiAiUEMiLA0KCSJwbGF0Zm9ybU9TIjog"
    "IldpbmRvd3MiLA0KCSJwbGF0Zm9ybU9TVmVyc2lvbiI6ICIxMC4wLjE5"
    "MDQyLjEuMjU2LjY0Yml0IiwNCgkicGxhdGZvcm1DaGlwc2V0IjogIlVua25vd24iDQp9"
)
USER_AGENT = "ShooterGame/13 Windows/10.0.19043.1.256.64bit"

_RE_PD = re.compile(r"pd\.([a-z0-9-]+)\.a\.pvp\.net/account-xp/v1/")
_RE_GLZ = re.compile(r"https://glz-([a-z0-9-]+)\.([a-z0-9-]+)\.a\.pvp\.net")
_RE_VERSION = re.compile(r"CI server version:\s*(\S+)")


class RiotError(Exception):
    """A request failed after all retries."""


class ValorantNotRunning(RiotError):
    """Riot client / VALORANT files are missing or not ready yet."""


def _local_app_data() -> str:
    return os.getenv("LOCALAPPDATA") or ""


class RiotClient:
    def __init__(self, lockfile_path=None, game_log_path=None, local_scheme="https", verify_remote=None, ws_port=None):
        base = _local_app_data()
        self.lockfile_path = lockfile_path or os.path.join(
            base, "Riot Games", "Riot Client", "Config", "lockfile"
        )
        self.game_log_path = game_log_path or os.path.join(
            base, "VALORANT", "Saved", "Logs", "ShooterGame.log"
        )
        self.local_scheme = local_scheme  # tests use "http"
        if verify_remote is None:
            verify_remote = os.getenv("VRY_INSECURE_TLS") != "1"

        self._local = requests.Session()
        self._local.verify = False  # localhost, self-signed
        self._remote = requests.Session()
        self._remote.verify = verify_remote
        for session in (self._local, self._remote):
            adapter = HTTPAdapter(pool_connections=4, pool_maxsize=16)
            session.mount("https://", adapter)
            session.mount("http://", adapter)

        self._gate = threading.BoundedSemaphore(MAX_IN_FLIGHT)
        self._auth_lock = threading.Lock()
        self._auth_gen = 0

        self.port = None
        self._ws_port = ws_port  # tests only; the client serves HTTP and WS on one port
        self._password = None
        self.headers = {}
        self.puuid = None
        self.urls = {}

    # ------------------------------------------------------------------ setup
    def connect(self):
        """Read lockfile, region, client version and an access token."""
        self._read_lockfile()
        pd, glz, version = self._scan_game_log()
        self.urls = {
            "pd": f"https://pd.{pd}.a.pvp.net",
            "glz": f"https://glz-{glz[0]}.{glz[1]}.a.pvp.net",
            "shared": f"https://shared.{pd}.a.pvp.net",
        }
        self._client_version = version
        self.headers, self.puuid = self._build_headers()
        log.info("connected: pd=%s glz=%s version=%s", self.urls["pd"], self.urls["glz"], version)

    def _read_lockfile(self):
        try:
            with open(self.lockfile_path) as f:
                parts = f.read().strip().split(":")
        except OSError as e:
            raise ValorantNotRunning("Riot client lockfile not found") from e
        if len(parts) < 5:
            raise ValorantNotRunning("Riot client lockfile is incomplete")
        self.port, self._password = parts[2], parts[3]

    def _local_auth(self):
        token = base64.b64encode(f"riot:{self._password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    @property
    def ws_port(self):
        return self._ws_port or self.port

    def local_auth_headers(self):
        return self._local_auth()

    def _scan_game_log(self, timeout=60):
        """Find region + client version in ShooterGame.log (bounded wait)."""
        try:
            f = open(self.game_log_path, "r", encoding="utf8", errors="replace")
        except OSError as e:
            raise ValorantNotRunning("VALORANT log file not found") from e
        pd = glz = version = None
        deadline = time.monotonic() + timeout
        with f:
            while time.monotonic() < deadline:
                line = f.readline()
                if not line:  # EOF: the game may still be writing
                    time.sleep(0.5)
                    continue
                if pd is None and (m := _RE_PD.search(line)):
                    pd = m.group(1)
                elif glz is None and (m := _RE_GLZ.search(line)):
                    glz = (m.group(1), m.group(2))
                elif version is None and (m := _RE_VERSION.search(line)):
                    version = m.group(1)
                if pd and glz and version:
                    break
        if not (pd and glz and version):
            raise ValorantNotRunning("could not read region/version from VALORANT log yet")
        if pd == "pbe":
            pd, glz = "na", ("na-1", "na")
        return pd, glz, version

    def _build_headers(self, timeout=60):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._read_lockfile()  # port can change if the client restarts
                r = self._local.get(
                    f"{self.local_scheme}://127.0.0.1:{self.port}/entitlements/v1/token",
                    headers=self._local_auth(),
                    timeout=TIMEOUT,
                )
                data = r.json()
            except ValorantNotRunning:
                raise
            except (requests.RequestException, ValueError):
                data = {}
            if isinstance(data, dict) and all(k in data for k in ("accessToken", "token", "subject")):
                headers = {
                    "Authorization": f"Bearer {data['accessToken']}",
                    "X-Riot-Entitlements-JWT": data["token"],
                    "X-Riot-ClientPlatform": CLIENT_PLATFORM,
                    "X-Riot-ClientVersion": self._client_version,
                    "User-Agent": USER_AGENT,
                }
                return headers, data["subject"]
            if time.monotonic() > deadline:
                raise ValorantNotRunning("entitlements token is not ready")
            time.sleep(1)

    def _refresh_auth(self, seen_gen):
        """Refresh tokens once, even if many threads notice the expiry together."""
        with self._auth_lock:
            if self._auth_gen != seen_gen:
                return
            self.headers, self.puuid = self._build_headers(timeout=30)
            self._auth_gen += 1
            log.info("access token refreshed")

    # --------------------------------------------------------------- requests
    def _request(self, method, url, **kwargs):
        last_exc = None
        for attempt in range(MAX_ATTEMPTS):
            gen = self._auth_gen
            try:
                with self._gate:
                    r = self._remote.request(
                        method, url, headers=self.headers, timeout=TIMEOUT, **kwargs
                    )
            except requests.RequestException as e:
                last_exc = e
                log.warning("%s %s failed (%s), attempt %d", method, _path(url), e, attempt + 1)
                time.sleep(min(2**attempt, 5))
                continue
            if r.status_code == 429:
                delay = _retry_after(r, attempt)
                log.warning("rate limited on %s, waiting %.1fs", _path(url), delay)
                time.sleep(delay)
                continue
            if r.status_code == 401 or (r.status_code == 400 and "BAD_CLAIMS" in r.text[:300]):
                self._refresh_auth(gen)
                continue
            return r
        raise RiotError(f"{method} {_path(url)}: giving up after {MAX_ATTEMPTS} attempts") from last_exc

    def _json(self, method, url, **kwargs):
        """Parsed JSON, or None for 404 / any non-OK / unparsable response."""
        r = self._request(method, url, **kwargs)
        if not r.ok:
            if r.status_code != 404:
                log.warning("%s %s -> HTTP %s", method, _path(url), r.status_code)
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def local_get(self, path, attempts=3):
        last = None
        for i in range(attempts):
            try:
                r = self._local.get(
                    f"{self.local_scheme}://127.0.0.1:{self.port}{path}",
                    headers=self._local_auth(),
                    timeout=5,
                )
                data = r.json() if r.ok else None
                if isinstance(data, dict) and data.get("errorCode") != "RPC_ERROR":
                    return data
            except (requests.RequestException, ValueError) as e:
                last = e
            time.sleep(1)
        raise RiotError(f"local {path} unavailable") from last

    # -------------------------------------------------------------- endpoints
    def presences(self):
        return self.local_get("/chat/v4/presences").get("presences", [])

    def mmr(self, puuid):
        return self._json("GET", f"{self.urls['pd']}/mmr/v1/players/{puuid}")

    def competitive_updates(self, puuid):
        return self._json(
            "GET",
            f"{self.urls['pd']}/mmr/v1/players/{puuid}/competitiveupdates"
            "?startIndex=0&endIndex=1&queue=competitive",
        )

    def match_details(self, match_id):
        return self._json("GET", f"{self.urls['pd']}/match-details/v1/matches/{match_id}")

    def names(self, puuids):
        """{puuid: 'Name#Tag'} via a single batched call."""
        data = self._json("PUT", f"{self.urls['pd']}/name-service/v2/players", json=list(puuids))
        if not isinstance(data, list):
            return {}
        return {
            p["Subject"]: f"{p.get('GameName', '')}#{p.get('TagLine', '')}"
            for p in data
            if "Subject" in p
        }

    def coregame_match_id(self):
        data = self._json("GET", f"{self.urls['glz']}/core-game/v1/players/{self.puuid}")
        return (data or {}).get("MatchID")

    def coregame_match(self, match_id):
        return self._json("GET", f"{self.urls['glz']}/core-game/v1/matches/{match_id}")

    def pregame_match_id(self):
        data = self._json("GET", f"{self.urls['glz']}/pregame/v1/players/{self.puuid}")
        return (data or {}).get("MatchID")

    def pregame_match(self, match_id):
        return self._json("GET", f"{self.urls['glz']}/pregame/v1/matches/{match_id}")

    def content(self):
        return self._json("GET", f"{self.urls['shared']}/content-service/v3/content")


def _retry_after(response, attempt):
    try:
        return min(float(response.headers.get("Retry-After")), 10.0)
    except (TypeError, ValueError):
        return min(2.0 * (attempt + 1), 10.0)


def _path(url):
    """URL without scheme/host for logging."""
    return "/" + url.split("/", 3)[-1] if "//" in url else url
