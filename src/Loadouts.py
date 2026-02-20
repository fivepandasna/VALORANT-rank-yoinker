import time
import requests
from colr import color
from src.constants import hide_names
import json


class Loadouts:
    def __init__(self, Requests, log, colors, Server, current_map):

        self.Requests = Requests
        self.log = log
        self.colors = colors
        self.Server = Server
        self.current_map = current_map

    def get_match_loadouts(self, match_id, players, weaponChoose, valoApiSkins, names, state="game"):
        playersBackup = players
        weaponLists = {}
        valApiWeapons = requests.get(
            "https://valorant-api.com/v1/weapons").json()
        if state == "game":
            team_id = "Blue"
            PlayerInventorys = self.Requests.fetch(
                "glz", f"/core-game/v1/matches/{match_id}/loadouts", "get")
        elif state == "pregame":
            pregame_stats = players
            players = players["AllyTeam"]["Players"]
            team_id = pregame_stats['Teams'][0]['TeamID']
            PlayerInventorys = self.Requests.fetch(
                "glz", f"/pregame/v1/matches/{match_id}/loadouts", "get")

        # CharacterID -> Loadout lookup
        loadout_by_character = {}
        for loadout_entry in PlayerInventorys["Loadouts"]:
            char_id = loadout_entry.get("CharacterID", "").lower()
            if char_id:
                loadout_by_character[char_id] = loadout_entry["Loadout"] if state == "game" else loadout_entry

        for player in players:
            char_id = player.get("CharacterID", "").lower()
            inv = loadout_by_character.get(char_id)
            if inv is None:
                continue
            for weapon in valApiWeapons["data"]:
                if weapon["displayName"].lower() == weaponChoose.lower():
                    skin_id = \
                        inv["Items"][weapon["uuid"].lower()]["Sockets"]["bcef87d6-209b-46c6-8b19-fbe40bd95abc"]["Item"][
                            "ID"]
                    json_data = valoApiSkins.json()

                    if "data" not in json_data:
                        self.log("Skins API response missing 'data'.")
                        return None

                    for skin in json_data["data"]:
                        if skin_id.lower() == skin["uuid"].lower():
                            rgb_color = self.colors.get_rgb_color_from_skin(
                                skin["uuid"].lower(), valoApiSkins)
                            skin_display_name = skin["displayName"].replace(
                                f" {weapon['displayName']}", "")
                            weaponLists.update({player["Subject"]: color(
                                skin_display_name, fore=rgb_color)})
        final_json = self.convertLoadoutToJsonArray(
            PlayerInventorys, playersBackup, state, names)
        self.Server.send_payload("matchLoadout", final_json)
        return [weaponLists, final_json]

    # this will convert valorant loadouts to json with player names
    def convertLoadoutToJsonArray(self, PlayerInventorys, players, state, names):
        valoApiAgents = requests.get("https://valorant-api.com/v1/agents")

        final_final_json = {"Players": {},
                            "time": int(time.time()),
                            "map": self.current_map}

        final_json = final_final_json["Players"]
        if state == "game":
            PlayerInventorys = PlayerInventorys["Loadouts"]

            # CharacterID -> Loadout lookup (if player has an agent != spectator)
            loadout_by_character = {}
            for entry in PlayerInventorys:
                char_id = entry.get("CharacterID", "").lower()
                if char_id:
                    loadout_by_character[char_id] = entry["Loadout"]

            # Parse once, build a uuid->agent dict for O(1) lookups
            agents_by_uuid = {a["uuid"]: a for a in valoApiAgents.json()["data"]}

            for player in players:
                subject = player["Subject"]
                char_id = player.get("CharacterID", "").lower()
                loadout_entry = loadout_by_character.get(char_id)

                final_json[subject] = {}

                # skip if not found
                if loadout_entry is None:
                    continue

                agent = agents_by_uuid.get(player["CharacterID"])

                # creates name field
                if hide_names:
                    if agent:
                        final_json[subject]["Name"] = agent["displayName"]
                else:
                    final_json[subject]["Name"] = names[subject]

                # creates team field
                final_json[subject]["Team"] = player["TeamID"]
                final_json[subject]["Level"] = player["PlayerIdentity"]["AccountLevel"]

                if agent:
                    final_json[subject]["AgentArtworkName"] = agent["displayName"] + "Artwork"
                    final_json[subject]["Agent"] = agent["displayIcon"]

        return final_final_json