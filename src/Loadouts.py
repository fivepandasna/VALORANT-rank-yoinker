import time
import requests
from src.colors import color
from src.constants import sockets, hide_names
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
        valApiWeapons = requests.get("https://valorant-api.com/v1/weapons").json()

        if state == "game":
            PlayerInventorys = self.Requests.fetch(
                "glz", f"/core-game/v1/matches/{match_id}/loadouts", "get")
        elif state == "pregame":
            pregame_stats = players
            players = players["AllyTeam"]["Players"]
            PlayerInventorys = self.Requests.fetch(
                "glz", f"/pregame/v1/matches/{match_id}/loadouts", "get")

        # subject (player UUID) -> loadout lookup
        loadout_by_subject = {}
        for loadout_entry in PlayerInventorys["Loadouts"]:
            subj = loadout_entry.get("Subject", "").lower()
            char_id = loadout_entry.get("CharacterID", "")
            if subj and char_id:
                loadout_by_subject[subj] = loadout_entry["Loadout"] if state == "game" else loadout_entry

        for player in players:
            subj = player.get("Subject", "").lower()
            inv = loadout_by_subject.get(subj)
            if inv is None:
                continue
            for weapon in valApiWeapons["data"]:
                if weapon["displayName"].lower() == weaponChoose.lower():
                    skin_id = inv["Items"][weapon["uuid"].lower()]["Sockets"]["bcef87d6-209b-46c6-8b19-fbe40bd95abc"]["Item"]["ID"]
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

        final_json = self.convertLoadoutToJsonArray(PlayerInventorys, playersBackup, state)
        self.Server.send_payload("matchLoadout", final_json)
        return [weaponLists, final_json]

    def convertLoadoutToJsonArray(self, PlayerInventorys, players, state):
        final_final_json = {
            "Players": {},
            "time": int(time.time()),
            "map": self.current_map
        }
        final_json = final_final_json["Players"]

        if state == "game":
            loadout_by_subject = {}
            for entry in PlayerInventorys["Loadouts"]:
                subj = entry.get("Subject", "").lower()
                char_id = entry.get("CharacterID", "")
                if subj and char_id:
                    loadout_by_subject[subj] = entry

            agents_by_uuid = {
                a["uuid"]: a
                for a in requests.get("https://valorant-api.com/v1/agents").json()["data"]
            }

            for player in players:
                puuid = player["Subject"]
                entry = loadout_by_subject.get(puuid.lower())
                if not entry:
                    continue
                agent = agents_by_uuid.get(player["CharacterID"])
                final_json[puuid] = {
                    "Agent": agent["displayIcon"] if agent else None
                }

        return final_final_json