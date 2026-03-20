# ui/campaign_config.py
from __future__ import annotations

import json
import os
from typing import Any, Dict


DEFAULT_CAMPAIGN_CONFIG: Dict[str, Any] = {
    "name": "",
    "ruleset": "default",
    "grid_ft": 5,
    "vision_rules": "standard",
}


def load_campaign_config(campaign_path: str) -> Dict[str, Any]:
    """
    Loads campaign-scoped configuration.

    Canonical path:
      campaigns/<campaign>/campaign.json

    Returns defaults if missing or malformed.
    """
    cfg = dict(DEFAULT_CAMPAIGN_CONFIG)

    if not campaign_path:
        return cfg

    path = os.path.join(campaign_path, "campaign.json")
    if not os.path.exists(path):
        return cfg

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            cfg.update(raw)
    except Exception:
        # Keep defaults on any error; don't crash the DM app for config issues.
        return cfg

    # Normalize a few expected types
    try:
        cfg["grid_ft"] = int(cfg.get("grid_ft", 5) or 5)
    except Exception:
        cfg["grid_ft"] = 5

    ruleset = str(cfg.get("ruleset", "default") or "default").strip()
    cfg["ruleset"] = ruleset if ruleset else "default"

    vision = str(cfg.get("vision_rules", "standard") or "standard").strip()
    cfg["vision_rules"] = vision if vision else "standard"

    name = str(cfg.get("name", "") or "").strip()
    cfg["name"] = name

    return cfg
