"""K/D and headshot % from a player's most recent competitive match."""
import logging
import threading
from collections import OrderedDict
from concurrent.futures import Future

log = logging.getLogger("vry.stats")

NO_STATS = {"kd": "N/A", "hs": "N/A"}


def compute_stats(puuid, match):
    """Headshot % and K/D for ``puuid`` in a /match-details response."""
    hits = headshots = 0
    for rnd in match.get("roundResults") or ():
        for ps in rnd.get("playerStats") or ():
            if ps.get("subject") != puuid:
                continue
            for dmg in ps.get("damage") or ():
                h = dmg.get("headshots", 0)
                hits += h + dmg.get("legshots", 0) + dmg.get("bodyshots", 0)
                headshots += h
            break  # one entry per player per round

    kills = deaths = 0
    for p in match.get("players") or ():
        if p.get("subject") == puuid:
            s = p.get("stats") or {}
            kills, deaths = s.get("kills", 0), s.get("deaths", 0)
            break

    return {
        "kd": round(kills / deaths, 2) if deaths else kills,
        "hs": round(headshots / hits * 100) if hits else "N/A",
    }


class MatchStats:
    """Fetches match details at most once per match, even across threads
    (premade teammates share their last match)."""

    def __init__(self, riot, max_cached=12):
        self.riot = riot
        self._cache = OrderedDict()
        self._inflight = {}
        self._lock = threading.Lock()
        self._max = max_cached

    def get(self, puuid):
        try:
            updates = self.riot.competitive_updates(puuid)
            matches = (updates or {}).get("Matches") or []
            match_id = matches[0].get("MatchID") if matches else None
            if not match_id:
                return dict(NO_STATS)
            match = self._match(match_id)
            return compute_stats(puuid, match) if match else dict(NO_STATS)
        except Exception as e:
            log.warning("stats unavailable for %s: %s", puuid, e)
            return dict(NO_STATS)

    def clear(self):
        with self._lock:
            self._cache.clear()

    def _match(self, match_id):
        with self._lock:
            if match_id in self._cache:
                self._cache.move_to_end(match_id)
                return self._cache[match_id]
            future = self._inflight.get(match_id)
            owner = future is None
            if owner:
                future = self._inflight[match_id] = Future()
        if not owner:
            return future.result(timeout=60)
        data = None
        try:
            data = self.riot.match_details(match_id)
        except Exception as e:
            log.warning("match details failed for %s: %s", match_id, e)
        finally:
            with self._lock:
                self._inflight.pop(match_id, None)
                if data:
                    self._cache[match_id] = data
                    while len(self._cache) > self._max:
                        self._cache.popitem(last=False)
            future.set_result(data)
        return data
