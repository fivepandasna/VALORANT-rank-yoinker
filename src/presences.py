import base64
import json
import time

class Presences:
    def __init__(self, Requests, log):
        self.Requests = Requests
        self.log = log

    def get_presence(self):
        presences = self.Requests.fetch(url_type="local", endpoint="/chat/v4/presences", method="get")
        if presences is None:
            return None
        return presences['presences']

    def get_game_state(self, presences):
        private_presence = self.get_private_presence(presences)
        if private_presence:
            # Temp fix: Riot is swapping between nested and flat API structures.
            # Check for nested structure.
            if "matchPresenceData" in private_presence:
                return private_presence["matchPresenceData"]["sessionLoopState"]
            # Check for flattened structure.
            elif "sessionLoopState" in private_presence:
                return private_presence["sessionLoopState"]
            else:
                # No known structure found, log and fail
                self.log("ERROR: Unknown presence API structure in 'get_game_state'.")
                return private_presence["matchPresenceData"]["sessionLoopState"]
        return None

    def get_private_presence(self, presences):
        for presence in presences:
            if presence['puuid'] == self.Requests.puuid:
                if presence.get("championId") is not None or presence.get("product") == "league_of_legends":
                    return None
                else:
                    if not presence['private']:
                        return None
                    decoded_private = json.loads(base64.b64decode(presence['private']))
                    return decoded_private
        return None

    def decode_presence(self, private):
        if private is None or str(private) == "":
            return {"isValid": False, "partyId": 0, "partySize": 0, "partyVersion": 0}
        # Already a decoded dict
        if isinstance(private, dict):
            return private if private.get("isValid") else {"isValid": False, "partyId": 0, "partySize": 0, "partyVersion": 0}
        try:
            decoded = json.loads(base64.b64decode(str(private)).decode("utf-8"))
            if decoded.get("isValid"):
                return decoded
        except Exception:
            pass
        return {"isValid": False, "partyId": 0, "partySize": 0, "partyVersion": 0}

    def wait_for_presence(self, PlayersPuuids):
        while True:
            presence = self.get_presence()
            for puuid in PlayersPuuids:
                if puuid not in str(presence):
                    time.sleep(1)
                    continue
            break