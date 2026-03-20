from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional

CONDITION_NAMES = {
    "prone",
    "restrained",
    "poisoned",
    "blinded",
    "stunned",
    "charmed",
}


def normalize_condition_name(value: str) -> str:
    text = str(value or "").strip().lower().replace("-", " ").replace("_", " ")
    aliases = {
        "poison": "poisoned",
        "blind": "blinded",
        "stun": "stunned",
        "restrain": "restrained",
        "charm": "charmed",
    }
    text = aliases.get(text, text)
    return text


def canonical_condition_record(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    src = dict(raw or {})
    name = normalize_condition_name(src.get("name") or src.get("condition") or src.get("condition_name"))
    if not name:
        raise ValueError("Condition name is required")

    rounds_remaining = src.get("rounds_remaining", src.get("duration_rounds", None))
    try:
        if rounds_remaining is not None:
            rounds_remaining = int(rounds_remaining)
            if rounds_remaining <= 0:
                rounds_remaining = None
    except Exception:
        rounds_remaining = None

    save_cfg = dict(src.get("save", {}) or {})
    ability = str(save_cfg.get("ability", src.get("save_ability", "")) or "").strip().upper()
    timing = str(save_cfg.get("timing", src.get("save_timing", "none")) or "none").strip().lower()
    mode = str(save_cfg.get("mode", src.get("save_mode", "normal")) or "normal").strip().lower()
    save_dc = save_cfg.get("dc", src.get("save_dc", None))
    try:
        save_dc = int(save_dc) if save_dc is not None else None
    except Exception:
        save_dc = None
    auto = bool(save_cfg.get("auto", src.get("save_auto", True)))
    pending_request_id = str(save_cfg.get("pending_request_id", src.get("pending_request_id", "")) or "").strip()

    tick_cfg = dict(src.get("tick_damage", {}) or {})
    tick_amount = tick_cfg.get("amount", src.get("tick_damage_amount", src.get("damage_each_turn", 0)))
    try:
        tick_amount = int(tick_amount or 0)
    except Exception:
        tick_amount = 0
    tick_timing = str(tick_cfg.get("timing", src.get("tick_timing", "none")) or "none").strip().lower()
    tick_type = str(tick_cfg.get("damage_type", src.get("damage_type", "")) or "").strip().lower()

    return {
        "condition_id": str(src.get("condition_id") or uuid.uuid4().hex[:12]),
        "name": name,
        "source": str(src.get("source") or ""),
        "rounds_remaining": rounds_remaining,
        "save": {
            "ability": ability,
            "dc": save_dc,
            "mode": mode if mode in {"normal", "advantage", "disadvantage"} else "normal",
            "timing": timing if timing in {"start", "end", "none"} else "none",
            "auto": auto,
            "pending_request_id": pending_request_id,
        },
        "tick_damage": {
            "amount": max(0, tick_amount),
            "timing": tick_timing if tick_timing in {"start", "end", "none"} else "none",
            "damage_type": tick_type,
        },
        "notes": str(src.get("notes") or ""),
        "meta": deepcopy(dict(src.get("meta", {}) or {})),
    }


SaveResolver = Callable[[str, int, str, str, Dict[str, Any]], Dict[str, Any]]


def process_turn_hook(*, actor: Any, timing: str, save_resolver: SaveResolver) -> Dict[str, Any]:
    """
    Deterministically process condition hooks for a token.

    Supported v1 features:
    - start/end-of-turn damage ticks
    - start/end-of-turn auto-resolved saves that remove the condition on success
    - end-of-turn duration decrement / expiration
    """
    hook = str(timing or "").strip().lower()
    if hook not in {"start", "end"}:
        return {"statuses": list(getattr(actor, "statuses", []) or []), "events": []}

    current = [canonical_condition_record(s) for s in list(getattr(actor, "statuses", []) or []) if isinstance(s, dict)]
    next_statuses: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []

    for cond in current:
        removed = False
        cid = str(cond.get("condition_id") or "")
        name = str(cond.get("name") or "condition")

        tick = dict(cond.get("tick_damage", {}) or {})
        if int(tick.get("amount", 0) or 0) > 0 and str(tick.get("timing", "none")) == hook:
            events.append({
                "event": "damage",
                "condition_id": cid,
                "condition_name": name,
                "amount": int(tick.get("amount", 0) or 0),
                "damage_type": str(tick.get("damage_type", "") or ""),
                "timing": hook,
            })

        save = dict(cond.get("save", {}) or {})
        ability = str(save.get("ability", "") or "").strip().upper()
        dc = save.get("dc", None)
        if (
            str(save.get("timing", "none")) == hook
            and ability
            and dc is not None
        ):
            pending_request_id = str(save.get("pending_request_id", "") or "").strip()
            if pending_request_id:
                events.append({
                    "event": "save_pending",
                    "condition_id": cid,
                    "condition_name": name,
                    "timing": hook,
                    "request_id": pending_request_id,
                })
            else:
                result = save_resolver(
                    ability,
                    int(dc),
                    str(save.get("mode", "normal") or "normal"),
                    f"{name.title()} Save",
                    {"condition_id": cid, "condition_name": name, "timing": hook},
                )
                result = deepcopy(dict(result or {}))
                if str(result.get("request_id", "") or "").strip() and not bool(result.get("resolved", False)):
                    cond.setdefault("save", {})["pending_request_id"] = str(result.get("request_id") or "").strip()
                    events.append({
                        "event": "save_requested",
                        "condition_id": cid,
                        "condition_name": name,
                        "timing": hook,
                        "request_id": str(result.get("request_id") or "").strip(),
                        "result": result,
                    })
                else:
                    events.append({
                        "event": "save",
                        "condition_id": cid,
                        "condition_name": name,
                        "timing": hook,
                        "result": result,
                    })
                    if bool(result.get("success", False)):
                        removed = True
                        events.append({
                            "event": "removed",
                            "condition_id": cid,
                            "condition_name": name,
                            "reason": "save_success",
                            "timing": hook,
                        })

        if removed:
            continue

        if hook == "end":
            rounds_remaining = cond.get("rounds_remaining", None)
            if rounds_remaining is not None:
                try:
                    rounds_remaining = int(rounds_remaining) - 1
                except Exception:
                    rounds_remaining = None
                if rounds_remaining is not None and rounds_remaining <= 0:
                    events.append({
                        "event": "expired",
                        "condition_id": cid,
                        "condition_name": name,
                        "reason": "duration_elapsed",
                        "timing": hook,
                    })
                    continue
                cond["rounds_remaining"] = rounds_remaining

        next_statuses.append(cond)

    return {"statuses": next_statuses, "events": events}
