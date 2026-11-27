"""Static game content: agents (name + icon), maps, current act and act labels.

valorant-api.com responses are cached on disk (24h) so startup is fast and
works offline; the season list from Riot is refreshed hourly so an act
rollover is picked up without restarting the app.
"""
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from src.constants import CACHE_DIR

log = logging.getLogger("vry.content")

VALORANT_API = "https://valorant-api.com/v1"
CACHE_TTL = 24 * 3600
SEASONS_TTL = 3600
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def _roman_to_int(roman):
    total = prev = 0
    for ch in reversed(roman.upper()):
        value = _ROMAN.get(ch)
        if value is None:
            return None
        total += -value if value < prev else value
        prev = value
    return total


def season_number(name):
    """'ACT III' -> 3, 'EPISODE 5' -> 5, new style 'V25-A1' -> 'v25-a1'."""
    if not name or not isinstance(name, str):
        return None
    parts = name.split()
    if not parts:
        return None
    last = parts[-1]
    if any(c.isalpha() for c in last) and any(c.isdigit() for c in last):
        return last.lower()
    upper = name.upper()
    if upper.startswith("EPISODE"):
        try:
            return int(last)
        except ValueError:
            return _roman_to_int(last)
    if upper.startswith("ACT"):
        roman = _roman_to_int(last)
        if roman is not None:
            return roman
        try:
            return int(last)
        except ValueError:
            return None
    return None


def parse_seasons(content):
    """-> (active act id or None, {act id: (episode, act)})."""
    active = None
    labels = {}
    episode = None
    for season in (content or {}).get("Seasons", []):
        kind = season.get("Type")
        if kind == "episode":
            episode = season_number(season.get("Name"))
        elif kind == "act":
            sid = season.get("ID", "").lower()
            labels[sid] = (episode, season_number(season.get("Name")))
            if active is None and season.get("IsActive"):
                active = sid
    return active, labels


class GameContent:
    def __init__(self, riot, cache_dir=None, http=None):
        self.riot = riot
        self.cache_dir = cache_dir or CACHE_DIR
        self.http = http or requests.Session()
        self.agents = {}  # uuid(lower) -> {"name", "icon"}
        self.maps = {}  # mapUrl(lower) -> display name
        self.season_id = None
        self.act_labels = {}
        self._seasons_loaded_at = 0.0

    def load(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            agents = pool.submit(self._load_agents)
            maps = pool.submit(self._load_maps)
            seasons = pool.submit(self._load_seasons)
            self.agents, self.maps = agents.result(), maps.result()
            seasons.result()

    def ensure_fresh(self):
        if time.monotonic() - self._seasons_loaded_at > SEASONS_TTL:
            self._load_seasons()

    def agent(self, character_id):
        return self.agents.get((character_id or "").lower(), {"name": "", "icon": None})

    def map_name(self, map_id):
        return self.maps.get((map_id or "").lower())

    # ------------------------------------------------------------------ loaders
    def _load_agents(self):
        try:
            data = self._cached("agents", f"{VALORANT_API}/agents?isPlayableCharacter=true")
            return {
                a["uuid"].lower(): {"name": a["displayName"], "icon": a.get("displayIcon")}
                for a in data["data"]
            }
        except Exception:
            log.exception("could not load agents")
            return {}

    def _load_maps(self):
        try:
            data = self._cached("maps", f"{VALORANT_API}/maps")
            return {m["mapUrl"].lower(): m["displayName"] for m in data["data"] if m.get("mapUrl")}
        except Exception:
            log.exception("could not load maps")
            return {}

    def _load_seasons(self):
        try:
            content = self.riot.content()
            active, labels = parse_seasons(content)
            if labels:
                self.season_id, self.act_labels = active, labels
                self._seasons_loaded_at = time.monotonic()
                log.info("current season id: %s", active)
        except Exception:
            log.exception("could not load seasons")

    def _cached(self, name, url):
        path = os.path.join(self.cache_dir, f"{name}.json")
        try:
            if time.time() - os.path.getmtime(path) < CACHE_TTL:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, ValueError):
            pass
        try:
            r = self.http.get(url, timeout=(5, 15))
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError):
            # Network failed: fall back to a stale cache if we have one.
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        except OSError:
            log.warning("could not write cache for %s", name)
        return data
