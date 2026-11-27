"""Decoding of the local client's chat presences.

Riot has shipped two shapes for the same data: flat keys, or grouped under
``matchPresenceData`` / ``partyPresenceData`` / ``playerPresenceData``.
``flatten`` makes both look the same so the rest of the code never branches.
"""
import base64
import json


def flatten(private):
    out = {}
    for key, value in (private or {}).items():
        if isinstance(value, dict) and key.endswith("PresenceData"):
            out.update(value)
        else:
            out[key] = value
    return out


def decode_private(presence):
    """Decode a presence entry's base64 ``private`` blob -> flat dict or None."""
    raw = presence.get("private")
    if not raw or "{" in str(raw):
        return None
    try:
        data = json.loads(base64.b64decode(raw))
    except (ValueError, TypeError):
        return None
    return flatten(data) if isinstance(data, dict) else None


def _is_valorant(presence):
    return presence.get("product") != "league_of_legends" and presence.get("championId") is None


def own_private(presences, puuid):
    for p in presences:
        if p.get("puuid") == puuid and _is_valorant(p):
            decoded = decode_private(p)
            if decoded:
                return decoded
    return None


def state_key(private):
    """(game state, party signature) or None. The party part only matters in
    the menus, where a changed party must refresh the table."""
    if not private:
        return None
    state = private.get("sessionLoopState")
    if not state:
        return None
    party = (private.get("partyId"), private.get("partySize")) if state == "MENUS" else None
    return (state, party)


def parse_event(raw, puuid):
    """State key from a websocket message, or None if it is not about us."""
    if puuid not in raw or "/chat/v4/presences" not in raw:
        return None
    try:
        message = json.loads(raw)
        payload = message[2]
        if payload.get("uri") != "/chat/v4/presences":
            return None
        presences = payload.get("data", {}).get("presences", [])
    except (ValueError, IndexError, TypeError, AttributeError):
        return None
    return state_key(own_private(presences, puuid))


def party_groups(presences, puuids):
    """Premade groups among ``puuids`` -> {party_id: [puuid, ...]} (2+ members)."""
    wanted = set(puuids)
    groups = {}
    for p in presences:
        subject = p.get("puuid")
        if subject not in wanted:
            continue
        decoded = decode_private(p)
        if decoded and decoded.get("isValid") and (decoded.get("partySize") or 0) > 1:
            groups.setdefault(decoded.get("partyId"), []).append(subject)
    return {pid: members for pid, members in groups.items() if len(members) > 1}


def party_members(presences, self_puuid):
    """Own party -> [{'puuid', 'level'}], self first."""
    me = own_private(presences, self_puuid)
    if not me or not me.get("isValid", True):
        return [{"puuid": self_puuid, "level": None}]
    party_id = me.get("partyId")
    members = [{"puuid": self_puuid, "level": me.get("accountLevel")}]
    for p in presences:
        subject = p.get("puuid")
        if subject == self_puuid or not _is_valorant(p):
            continue
        decoded = decode_private(p)
        if decoded and decoded.get("isValid") and decoded.get("partyId") == party_id:
            members.append({"puuid": subject, "level": decoded.get("accountLevel")})
    return members
