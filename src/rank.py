"""Turns a /mmr response into the few numbers the GUI shows."""
from src.constants import BEFORE_ASCENDANT_SEASONS

UNRANKED_TIERS = (0, 1, 2)


def _act_label(labels, season_id):
    episode, act = labels.get(season_id, (None, None))
    if act is None:
        return ""
    if episode is None:
        return f"(a{act})"
    if any(c.isalpha() for c in str(episode)):
        return f"({episode}a{act})"
    return f"(e{episode}a{act})"


def parse_rank(mmr, season_id, act_labels):
    """-> dict(rank, rr, peak, peak_act, wr, games). Never raises.

    rank / peak are competitive tier numbers (0 = unranked, 27 = Radiant).
    wr is an int percentage or "N/A".
    """
    comp = ((mmr or {}).get("QueueSkills") or {}).get("competitive") or {}
    seasons = comp.get("SeasonalInfoBySeasonID") or {}
    current = seasons.get(season_id) or {}

    try:
        tier = int(current.get("CompetitiveTier") or 0)
    except (TypeError, ValueError):
        tier = 0
    rank = tier if tier not in UNRANKED_TIERS else 0
    rr = (current.get("RankedRating") or 0) if rank else 0

    # Peak = highest tier with at least one win, across all seasons.
    peak, peak_season = rank, season_id
    for sid, info in seasons.items():
        for won_tier in (info or {}).get("WinsByTier") or ():
            try:
                t = int(won_tier)
            except (TypeError, ValueError):
                continue
            if sid in BEFORE_ASCENDANT_SEASONS and t > 20:
                t += 3
            if t > peak:
                peak, peak_season = t, sid

    games = current.get("NumberOfGames") or 0
    wins = current.get("NumberOfWinsWithPlacements")
    wr = int(wins / games * 100) if games and wins is not None else "N/A"

    return {
        "rank": rank,
        "rr": rr,
        "peak": peak,
        "peak_act": _act_label(act_labels, peak_season) if peak else "",
        "wr": wr,
        "games": games,
    }
