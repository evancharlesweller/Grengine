# net/server_client.py
import requests
from typing import Any, Dict, List, Optional


class ServerClient:
    """
    Thin HTTP client for the FastAPI roll/portal server.

    Goal:
      - keep requests/URLs/timeouts out of MainWindow
      - keep call signatures stable and minimal
      - never crash the UI (return empty {} / [] / False)
    """

    def __init__(self, *, base_url: str, campaign_id: str, timeout_s: float = 2.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.campaign_id = (campaign_id or "Test").strip() or "Test"
        self.timeout_s = float(timeout_s)

    def api(self, route: str) -> str:
        route = (route or "").strip()
        if not route.startswith("/"):
            route = "/" + route
        return f"{self.base_url}/api/campaigns/{self.campaign_id}{route}"

    # ---- Pending attacks ----
    def register_pending_attack(self, pending_attack: Dict[str, Any]) -> bool:
        try:
            payload = {
                "pending_attack_id": pending_attack["pending_attack_id"],
                "encounter_id": pending_attack.get("encounter_id", ""),

                "attacker_token_id": pending_attack["attacker_token_id"],
                "attacker_name": pending_attack.get("attacker_name", ""),
                "attacker_character_id": pending_attack.get("attacker_character_id", ""),
                "attacker_player_id": pending_attack["player_id"],
                "damage_expr": pending_attack.get("damage_expr", ""),

                "target_token_id": pending_attack.get("target_token_id", ""),
                "target_name": pending_attack.get("target_name", ""),
                "target_character_id": pending_attack.get("target_character_id", ""),

                "weapon_id": pending_attack.get("weapon_ref", "") or pending_attack.get("weapon_id", ""),
                "weapon_name": pending_attack.get("weapon_name", ""),
                "roll_mode": pending_attack.get("roll_mode", "normal"),

                "expires_in_sec": int(pending_attack.get("expires_in_sec", 90)),
            }
            r = requests.post(self.api("/pending_attacks"), json=payload, timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] register_pending_attack failed:", r.status_code, r.text)
                return False
            return True
        except Exception as e:
            print("[SERVER] register_pending_attack exception:", e)
            return False

    def cancel_pending_attack(self, pending_attack_id: str) -> None:
        if not pending_attack_id:
            return
        try:
            requests.delete(self.api(f"/pending_attacks/{pending_attack_id}"), timeout=self.timeout_s)
        except Exception as e:
            print("[SERVER] cancel_pending_attack exception:", e)

    # ---- DM polling ----
    def fetch_rolls(self) -> List[Dict[str, Any]]:
        try:
            r = requests.get(self.api("/next_rolls"), timeout=self.timeout_s)
            if not r.ok:
                print("Error fetching rolls:", r.status_code, r.text)
                return []
            data = r.json()
            if data:
                print("Received rolls:", data)
            return data if isinstance(data, list) else []
        except Exception as e:
            print("Failed to fetch rolls:", e)
            return []

    def fetch_damage_rolls(self) -> List[Dict[str, Any]]:
        try:
            r = requests.get(self.api("/next_damage_rolls"), timeout=self.timeout_s)
            if not r.ok:
                print("Error fetching damage rolls:", r.status_code, r.text)
                return []
            data = r.json()
            if data:
                print("Received damage rolls:", data)
            return data if isinstance(data, list) else []
        except Exception as e:
            print("Failed to fetch damage rolls:", e)
            return []

    def register_roll_request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Register a generic player-facing roll request (Phase D1 foundation)."""
        try:
            payload = {
                "request_id": (req.get("request_id") or "").strip(),
                "character_id": (req.get("character_id") or "").strip(),
                "player_id": (req.get("player_id") or "").strip(),
                "roll_kind": (req.get("roll_kind") or "save").strip(),
                "expected_sides": int(req.get("expected_sides", 20) or 20),
                "expected_count_min": int(req.get("expected_count_min", 1) or 1),
                "expected_count_max": int(req.get("expected_count_max", 1) or 1),
                "adv_mode": (req.get("adv_mode") or "normal").strip(),
                "dc": req.get("dc", None),
                "label": (req.get("label") or "").strip(),
                "context": req.get("context") or {},
                "ttl_s": int(req.get("ttl_s", 90) or 90),
            }
            r = requests.post(self.api("/roll_requests"), json=payload, timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] register_roll_request failed:", r.status_code, r.text)
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print("[SERVER] register_roll_request exception:", e)
            return {}

    def cancel_roll_request(self, request_id: str) -> None:
        request_id = (request_id or "").strip()
        if not request_id:
            return
        try:
            requests.delete(self.api(f"/roll_requests/{request_id}"), timeout=self.timeout_s)
        except Exception as e:
            print("[SERVER] cancel_roll_request exception:", e)

    def fetch_roll_request_results(self) -> list[Dict[str, Any]]:
        try:
            r = requests.get(self.api("/next_roll_request_results"), timeout=self.timeout_s)
            if not r.ok:
                print("Error fetching roll request results:", r.status_code, r.text)
                return []
            data = r.json()
            if data:
                print("Received roll request results:", data)
            return data if isinstance(data, list) else []
        except Exception as e:
            print("Failed to fetch roll request results:", e)
            return []

    def consume_spell_slot(self, *, slot_level: int, count: int = 1, spell_id: str = "") -> Dict[str, Any]:
        try:
            payload = {"slot_level": int(slot_level), "count": int(count), "spell_id": (spell_id or "").strip()}
            r = requests.post(self.api("/spell_slots/mine/consume"), json=payload, timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] consume_spell_slot failed:", r.status_code, r.text)
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print("[SERVER] consume_spell_slot exception:", e)
            return {}

    def fetch_spells(self) -> List[Dict[str, Any]]:
        try:
            r = requests.get(self.api("/spells"), timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] fetch_spells failed:", r.status_code, r.text)
                return []
            data = r.json()
            spells = data.get("spells", []) if isinstance(data, dict) else []
            return spells if isinstance(spells, list) else []
        except Exception as e:
            print("[SERVER] fetch_spells exception:", e)
            return []

    def fetch_spell_declarations(self) -> List[Dict[str, Any]]:
        try:
            r = requests.get(self.api("/next_spell_declarations"), timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] fetch_spell_declarations failed:", r.status_code, r.text)
                return []
            data = r.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            print("[SERVER] fetch_spell_declarations exception:", e)
            return []


    def fetch_reaction_responses(self) -> List[Dict[str, Any]]:
        try:
            r = requests.get(self.api("/next_reaction_responses"), timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] fetch_reaction_responses failed:", r.status_code, r.text)
                return []
            data = r.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            print("[SERVER] fetch_reaction_responses exception:", e)
            return []

    def use_reaction_spell(self, character_id: str, *, spell_id: str, slot_level: int = 0, note: str = "") -> Dict[str, Any]:
        character_id = (character_id or "").strip()
        if not character_id:
            return {}
        try:
            payload = {"spell_id": spell_id or "", "slot_level": int(slot_level or 0), "note": note or ""}
            r = requests.post(self.api(f"/characters/{character_id}/use_reaction_spell"), json=payload, timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] use_reaction_spell failed:", r.status_code, r.text)
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print("[SERVER] use_reaction_spell exception:", e)
            return {}

    def fetch_reaction_spell_declarations(self) -> List[Dict[str, Any]]:
        try:
            r = requests.get(self.api("/next_reaction_spell_declarations"), timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] fetch_reaction_spell_declarations failed:", r.status_code, r.text)
                return []
            data = r.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            print("[SERVER] fetch_reaction_spell_declarations exception:", e)
            return []

    # ---- Optional DM posts ----
    def post_attack_result(self, result: Dict[str, Any]) -> bool:
        try:
            r = requests.post(self.api("/attack_results"), json=result, timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] post_attack_result failed:", r.status_code, r.text)
                return False
            return True
        except Exception as e:
            print("[SERVER] post_attack_result exception:", e)
            return False

    # ---- Handouts (DM push) ----
    def push_handout(
        self,
        *,
        to_player_id: str,
        title: str,
        body: str,
        to_character_id: str = "",
        kind: str = "handout",
        payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """DM push a handout/readable to a player (no auth required server-side)."""
        try:
            payload = {
                "player_id": (to_player_id or "").strip(),
                "character_id": (to_character_id or "").strip(),
                "title": title or "",
                "text": body or "",
                "kind": kind or "handout",
                "payload": payload or {},
            }
            if not payload["player_id"]:
                return False
            r = requests.post(self.api("/handouts"), json=payload, timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] push_handout failed:", r.status_code, r.text)
                return False
            return True
        except Exception as e:
            print("[SERVER] push_handout exception:", e)
            return False

    # ---- Handout templates (DM) ----
    def fetch_handout_templates(self) -> List[Dict[str, Any]]:
        try:
            r = requests.get(self.api("/handout_templates"), timeout=self.timeout_s)
            if not r.ok:
                return []
            data = r.json()
            if isinstance(data, dict):
                data = data.get("templates", [])
            return data if isinstance(data, list) else []
        except Exception as e:
            print("[SERVER] fetch_handout_templates exception:", e)
            return []

    def upsert_handout_template(self, tpl: Dict[str, Any]) -> bool:
        try:
            r = requests.post(self.api("/handout_templates"), json=tpl, timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] upsert_handout_template failed:", r.status_code, r.text)
                return False
            return True
        except Exception as e:
            print("[SERVER] upsert_handout_template exception:", e)
            return False

    def delete_handout_template(self, template_id: str) -> bool:
        template_id = (template_id or "").strip()
        if not template_id:
            return False
        try:
            r = requests.delete(self.api(f"/handout_templates/{template_id}"), timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] delete_handout_template failed:", r.status_code, r.text)
                return False
            return True
        except Exception as e:
            print("[SERVER] delete_handout_template exception:", e)
            return False

    # ---- Players index (DM convenience) ----
    def fetch_players_index(self) -> List[Dict[str, Any]]:
        """Returns [{player_id, characters:[{character_id, display_name}]}]."""
        try:
            r = requests.get(self.api("/players"), timeout=self.timeout_s)
            if not r.ok:
                return []
            data = r.json()
            if isinstance(data, dict):
                data = data.get("players", [])
            return data if isinstance(data, list) else []
        except Exception as e:
            print("[SERVER] fetch_players_index exception:", e)
            return []

    # ---- Character sheets ----
    def get_character_sheet(self, character_id: str) -> Dict[str, Any]:
        character_id = (character_id or "").strip()
        if not character_id:
            return {}
        try:
            r = requests.get(self.api(f"/characters/{character_id}"), timeout=self.timeout_s)
            if not r.ok:
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print("[SERVER] fetch_character_sheet exception:", e)
            return {}

    def update_character_effects(self, character_id: str, effects: list[dict]) -> Dict[str, Any]:
        character_id = (character_id or "").strip()
        if not character_id:
            return {}
        try:
            payload = {"effects": effects if isinstance(effects, list) else []}
            r = requests.post(self.api(f"/characters/{character_id}/combat_effects"), json=payload, timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] update_character_effects failed:", r.status_code, r.text)
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print("[SERVER] update_character_effects exception:", e)
            return {}


    def update_character_death_saves(self, character_id: str, *, successes: int, failures: int) -> Dict[str, Any]:
        character_id = (character_id or "").strip()
        if not character_id:
            return {}
        try:
            sheet = self.get_character_sheet(character_id) or {}
            combat = dict((sheet.get("combat") or {})) if isinstance(sheet, dict) else {}
            combat["death_saves"] = {
                "successes": max(0, min(int(successes), 3)),
                "failures": max(0, min(int(failures), 3)),
            }
            payload = {"combat": combat}
            r = requests.post(self.api(f"/characters/{character_id}"), json=payload, timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] update_character_death_saves failed:", r.status_code, r.text)
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print("[SERVER] update_character_death_saves exception:", e)
            return {}

    def apply_damage_to_character(
        self,
        character_id: str,
        amount: int,
        *,
        token_id: str = "",
        encounter_id: str = "",
        pending_attack_id: str = "",
    ) -> Dict[str, Any]:
        character_id = (character_id or "").strip()
        if not character_id:
            return {}
        try:
            req = {
                "amount": int(amount),
                "source": "attack",
                "encounter_id": encounter_id or "",
                "token_id": token_id or "",
                "pending_attack_id": pending_attack_id or "",
            }
            r = requests.post(self.api(f"/characters/{character_id}/apply_damage"), json=req, timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] apply_damage_to_character failed:", r.status_code, r.text)
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print("[SERVER] apply_damage_to_character exception:", e)
            return {}

    # ---- Messages / toasts ----
    def post_message(
        self,
        player_id: str,
        text: str,
        kind: str = "info",
        *,
        ttl_seconds: int = 120,
        data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        player_id = (player_id or "").strip()
        if not player_id:
            return False
        try:
            payload = {
                "player_id": player_id,
                "kind": kind,
                "text": text,
                "ttl_seconds": int(ttl_seconds),
                "data": data or {},
            }
            r = requests.post(self.api("/messages"), json=payload, timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] post_message failed:", r.status_code, r.text)
                return False
            return True
        except Exception as e:
            print("[SERVER] post_message exception:", e)
            return False
        
    def set_character_hp(
        self,
        character_id: str,
        *,
        current_hp: int,
        temp_hp: int = 0,
        token_id: str = "",
        encounter_id: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        character_id = (character_id or "").strip()
        if not character_id:
            return {}
        try:
            req = {
                "current_hp": int(current_hp),
                "temp_hp": int(temp_hp),
                "encounter_id": encounter_id or "",
                "token_id": token_id or "",
                "reason": reason or "",
            }
            r = requests.post(self.api(f"/characters/{character_id}/set_hp"), json=req, timeout=self.timeout_s)
            if not r.ok:
                print("[SERVER] set_character_hp failed:", r.status_code, r.text)
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print("[SERVER] set_character_hp exception:", e)
            return {}


    def rest_control_start(self, rest_type: str) -> Dict[str, Any]:
        try:
            r = requests.post(self.api('/rest/control/start'), json={'rest_type': rest_type}, timeout=self.timeout_s)
            if not r.ok:
                print('[SERVER] rest_control_start failed:', r.status_code, r.text)
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print('[SERVER] rest_control_start exception:', e)
            return {}

    def rest_control_resolve(self, rest_type: str) -> Dict[str, Any]:
        try:
            r = requests.post(self.api('/rest/control/resolve'), json={'rest_type': rest_type}, timeout=self.timeout_s)
            if not r.ok:
                print('[SERVER] rest_control_resolve failed:', r.status_code, r.text)
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print('[SERVER] rest_control_resolve exception:', e)
            return {}

    def rest_control_cancel(self, rest_type: str) -> Dict[str, Any]:
        try:
            r = requests.post(self.api('/rest/control/cancel'), json={'rest_type': rest_type}, timeout=self.timeout_s)
            if not r.ok:
                print('[SERVER] rest_control_cancel failed:', r.status_code, r.text)
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print('[SERVER] rest_control_cancel exception:', e)
            return {}

    def rest_control_status(self) -> Dict[str, Any]:
        try:
            r = requests.get(self.api('/rest/control/status'), timeout=self.timeout_s)
            if not r.ok:
                print('[SERVER] rest_control_status failed:', r.status_code, r.text)
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print('[SERVER] rest_control_status exception:', e)
            return {}
    
    def levelup_grant_party(self) -> Dict[str, Any]:
        try:
            r = requests.post(self.api('/dm/levelup/grant_party'), json={}, timeout=self.timeout_s)
            if not r.ok:
                print('[SERVER] levelup_grant_party failed:', r.status_code, r.text)
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print('[SERVER] levelup_grant_party exception:', e)
            return {}

    def levelup_grant_character(self, character_id: str) -> Dict[str, Any]:
        character_id = (character_id or "").strip()
        if not character_id:
            return {}
        try:
            r = requests.post(
                self.api('/dm/levelup/grant_character'),
                json={'character_id': character_id},
                timeout=self.timeout_s
            )
            if not r.ok:
                print('[SERVER] levelup_grant_character failed:', r.status_code, r.text)
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print('[SERVER] levelup_grant_character exception:', e)
            return {}


    def levelup_active_characters(self) -> Dict[str, Any]:
        try:
            r = requests.get(self.api('/dm/levelup/active_characters'), timeout=self.timeout_s)
            if not r.ok:
                print('[SERVER] levelup_active_characters failed:', r.status_code, r.text)
                return {}
            data = r.json()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print('[SERVER] levelup_active_characters exception:', e)
            return {}
def upsert_character_sheet(self, character_id: str, payload: dict) -> Optional[dict]:
    """Create or update a character sheet on the server.

    This maps to:
      POST /api/campaigns/{campaign_id}/characters/{character_id}
    """
    try:
        url = f"{self.base_url}/api/campaigns/{self.campaign_id}/characters/{character_id}"
        r = requests.post(url, json=payload, timeout=5)
        if r.status_code >= 400:
            print(f"[ServerClient] upsert_character_sheet failed {r.status_code}: {r.text}")
            return None
        return r.json()
    except Exception as e:
        print(f"[ServerClient] upsert_character_sheet error: {e}")
        return None

def ensure_character_sheet_initialized(
    self,
    character_id: str,
    *,
    base_stats: dict,
    resources: dict,
    prefer_token_max_hp: bool = True,
    prefer_token_ac: bool = True,
    prefer_token_hp_when_sheet_default: bool = True,
) -> Optional[dict]:
    """Ensure a sheet exists and is initialized with sane stats.

    Problem this prevents:
    - brand new sheet defaults to 10 max_hp (see ensure_sheet_minimum), which can clobber token templates.

    Strategy:
    - If sheet missing: upsert with provided base_stats/resources.
    - If sheet exists but looks like defaults (e.g., max_hp == 10) and token has a larger max_hp, update max_hp
      (and hp optionally) without overwriting non-default sheets.
    """
    sheet = self.get_character_sheet(character_id)
    token_max_hp = int((resources or {}).get("max_hp", 0) or 0)
    token_hp = int((resources or {}).get("hp", token_max_hp) or token_max_hp)
    token_ac = int((base_stats or {}).get("ac", 10) or 10)

    if sheet is None:
        payload = {
            "character_id": character_id,
            "base_stats": base_stats or {},
            "resources": resources or {},
        }
        return self.upsert_character_sheet(character_id, payload)

    # Existing sheet -> only "upgrade" obvious defaults
    sheet_res = (sheet or {}).get("resources", {}) or {}
    sheet_base = (sheet or {}).get("base_stats", {}) or {}
    sheet_max_hp = int(sheet_res.get("max_hp", 0) or 0)
    sheet_hp = int(sheet_res.get("hp", 0) or 0)
    sheet_ac = int(sheet_base.get("ac", 10) or 10)

    patch = {"character_id": character_id}

    changed = False

    if prefer_token_max_hp and token_max_hp > 0 and (sheet_max_hp in (0, 10)) and (token_max_hp != sheet_max_hp):
        patch.setdefault("resources", {})["max_hp"] = token_max_hp
        changed = True

        if prefer_token_hp_when_sheet_default:
            # If sheet HP is also default-ish, align to token HP (clamped)
            if sheet_hp in (0, 10) and token_hp > 0:
                patch.setdefault("resources", {})["hp"] = max(0, min(token_hp, token_max_hp))
                changed = True

    if prefer_token_ac and token_ac > 0 and (sheet_ac in (0, 10)) and (token_ac != sheet_ac):
        patch.setdefault("base_stats", {})["ac"] = token_ac
        changed = True

    if not changed:
        return sheet

    return self.upsert_character_sheet(character_id, patch)

def set_character_hp(
    self,
    character_id: str,
    current_hp: int,
    *,
    temp_hp: int | None = None,
    source: str = "dm_set_hp",
    token_id: str = "",
    encounter_id: str = "",
) -> Dict[str, Any]:
    character_id = (character_id or "").strip()
    if not character_id:
        return {}
    try:
        req = {
            "current_hp": int(current_hp),
            "temp_hp": (int(temp_hp) if temp_hp is not None else None),
            "source": source,
            "encounter_id": encounter_id or "",
            "token_id": token_id or "",
        }
        r = requests.post(self.api(f"/characters/{character_id}/set_hp"), json=req, timeout=self.timeout_s)
        if not r.ok:
            print("[SERVER] set_character_hp failed:", r.status_code, r.text)
            return {}
        data = r.json()
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print("[SERVER] set_character_hp exception:", e)
        return {}
