"""Builds the "heartbeat" the GUI renders for the current game state.

Per refresh: one batched name lookup, one presence read, and one task per
player (rank + stats) all running concurrently, so the whole board takes
about as long as the slowest single player instead of the sum of all of them.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from src.constants import HIDE_LEVELS, HIDE_NAMES
from src.presence import party_groups, party_members
from src.rank import parse_rank
from src.riot import RiotError
from src.stats import MatchStats

log = logging.getLogger("vry.tracker")

WORKERS = 8
EMPTY_HEARTBEAT = {"state": None, "puuid": None, "map": None, "players": {}}


class Token:
    """Lets a newer refresh cancel an older one that is still running."""

    cancelled = False


class Tracker:
    def __init__(self, riot, content, send):
        self.riot = riot
        self.content = content
        self.send = send
        self.stats = MatchStats(riot)
        self._pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="vry")
        self._players = {}  # puuid -> (rank dict, stats dict) for the current match
        self._match_id = None

    def close(self):
        self._pool.shutdown(wait=False, cancel_futures=True)

    def reset(self):
        self._players = {}
        self._match_id = None
        self.stats.clear()

    # ------------------------------------------------------------------ refresh
    def refresh(self, state, token):
        """Build and send the heartbeat for ``state`` (runs in a worker thread)."""
        if state == "MENUS":
            self.reset()
            self.content.ensure_fresh()
        for attempt in range(3):
            try:
                heartbeat = self.build(state, token)
            except RiotError as e:
                log.warning("refresh failed (%s), attempt %d", e, attempt + 1)
                heartbeat = None
            except Exception:
                log.exception("unexpected error building %s board", state)
                return
            if token.cancelled:
                return
            if heartbeat is not None:
                self.send(heartbeat)
                return
            time.sleep(1.5)  # the match may not be visible to the API yet
        log.warning("no data for state %s after retries", state)

    def build(self, state, token):
        if state == "INGAME":
            match_id = self.riot.coregame_match_id()
            match = match_id and self.riot.coregame_match(match_id)
            if not match:
                return None
            entries = [
                {
                    "puuid": p["Subject"],
                    "team": p.get("TeamID"),
                    "agent": p.get("CharacterID"),
                    **_identity(p),
                }
                for p in match.get("Players", [])
            ]
            return self._assemble(state, match_id, match.get("MapID"), entries, token)

        if state == "PREGAME":
            match_id = self.riot.pregame_match_id()
            match = match_id and self.riot.pregame_match(match_id)
            if not match:
                return None
            entries = [
                {
                    "puuid": p["Subject"],
                    "team": None,  # GUI groups pregame players as "allies"
                    "agent": p.get("CharacterID"),
                    **_identity(p),
                }
                for p in (match.get("AllyTeam") or {}).get("Players", [])
            ]
            return self._assemble(state, match_id, match.get("MapID"), entries, token)

        if state == "MENUS":
            members = party_members(self.riot.presences(), self.riot.puuid)
            entries = [
                {
                    "puuid": m["puuid"],
                    "team": None,
                    "agent": None,
                    "level": m["level"],
                    "incognito": False,
                    "hide_level": False,
                }
                for m in members
            ]
            return self._assemble(state, None, None, entries, token)

        return None

    # ----------------------------------------------------------------- assemble
    def _assemble(self, state, match_id, map_id, entries, token):
        if match_id and match_id != self._match_id:
            self._players = {}
            self._match_id = match_id
        puuids = [e["puuid"] for e in entries]

        names_f = self._pool.submit(self.riot.names, puuids)
        presence_f = self._pool.submit(self._presences_for, puuids, token)
        data_f = {p: self._pool.submit(self._player_data, p) for p in puuids}

        names = names_f.result()
        presences = presence_f.result()
        me = self.riot.puuid
        my_party = {m["puuid"] for m in party_members(presences, me)}
        groups = party_groups(presences, puuids) if state != "MENUS" else {}
        member_party = {m: pid for pid, members in groups.items() for m in members}

        def shown_level(e):
            hidden = (
                HIDE_LEVELS and e["hide_level"] and e["puuid"] != me and e["puuid"] not in my_party
            )
            return None if hidden else e["level"]

        entries.sort(key=lambda e: shown_level(e) or -1, reverse=True)

        party_numbers = {}
        players = {}
        for e in entries:
            puuid = e["puuid"]
            rank, stats = data_f[puuid].result()
            agent = self.content.agent(e["agent"])

            name = names.get(puuid, "Unknown")
            if HIDE_NAMES and e["incognito"] and puuid != me and puuid not in my_party:
                name = agent["name"] or "Player"

            pid = member_party.get(puuid)
            if pid is not None and pid not in party_numbers:
                party_numbers[pid] = len(party_numbers) + 1

            players[puuid] = {
                "name": name,
                "team": e["team"],
                "agent": agent["name"],
                "agentImgLink": agent["icon"],
                "rank": rank["rank"],
                "rr": rank["rr"],
                "peakRank": rank["peak"],
                "peakRankAct": rank["peak_act"],
                "winPercentage": f"{rank['wr']} ({rank['games']})",
                "kd": stats["kd"],
                "headshotPercentage": stats["hs"],
                "level": shown_level(e),
                "partyNumber": party_numbers.get(pid, 0),
            }

        return {
            "state": state,
            "puuid": me,
            "map": self.content.map_name(map_id),
            "players": players,
        }

    # ------------------------------------------------------------------ workers
    def _player_data(self, puuid):
        cached = self._players.get(puuid)
        if cached:
            return cached
        try:
            mmr = self.riot.mmr(puuid)
        except RiotError as e:
            log.warning("rank unavailable for %s: %s", puuid, e)
            mmr = None
        result = (
            parse_rank(mmr, self.content.season_id, self.content.act_labels),
            self.stats.get(puuid),
        )
        if mmr is not None:  # don't pin a failed lookup for the whole match
            self._players[puuid] = result
        return result

    def _presences_for(self, puuids, token, tries=3):
        """Presences of everyone in the match (they can lag a moment)."""
        wanted = set(puuids)
        presences = []
        for _ in range(tries):
            try:
                presences = self.riot.presences()
            except RiotError:
                presences = []
            if wanted <= {p.get("puuid") for p in presences} or token.cancelled:
                break
            time.sleep(0.75)
        return presences


def _identity(player):
    ident = player.get("PlayerIdentity") or {}
    return {
        "level": ident.get("AccountLevel"),
        "incognito": bool(ident.get("Incognito")),
        "hide_level": bool(ident.get("HideAccountLevel")),
    }
