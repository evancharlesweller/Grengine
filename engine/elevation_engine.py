from __future__ import annotations

from typing import Any, Dict, Tuple

# Elevation is stored in map meta as: meta["elevation"][f"{x},{y}"] = int(feet)
# Missing entries imply 0 ft.

def get_cell_elevation_ft(meta: Dict[str, Any], gx: int, gy: int) -> int:
    try:
        elev = (meta or {}).get("elevation", {}) or {}
        if not isinstance(elev, dict):
            return 0
        v = elev.get(f"{int(gx)},{int(gy)}", 0)
        return int(v or 0)
    except Exception:
        return 0

def compute_drop_ft(meta: Dict[str, Any], from_gx: int, from_gy: int, to_gx: int, to_gy: int) -> int:
    """Positive means a downward drop in feet."""
    a = get_cell_elevation_ft(meta, int(from_gx), int(from_gy))
    b = get_cell_elevation_ft(meta, int(to_gx), int(to_gy))
    return max(0, int(a) - int(b))

def falling_damage_dice(drop_ft: int) -> str:
    """RAW 5e: 1d6 per 10 ft, max 20d6 (200 ft)."""
    try:
        d = int(drop_ft or 0)
    except Exception:
        d = 0
    n = max(0, d // 10)
    n = min(20, n)
    if n <= 0:
        return "0"
    return f"{n}d6"

def falling_save_dc(drop_ft: int) -> int:
    """Variant DC: 10 + 1 per 10 ft (cap 25)."""
    try:
        d = int(drop_ft or 0)
    except Exception:
        d = 0
    dc = 10 + max(0, d // 10)
    return min(25, max(10, dc))


def get_drop_edge_drop_ft(meta: Dict[str, Any], from_gx: int, from_gy: int, to_gx: int, to_gy: int) -> int:
    """Return drop_ft if crossing a painted DROP EDGE from (from_gx,from_gy) toward (to_gx,to_gy).

    Drop edges are stored as meta["drop_edges"] = [{"x":int,"y":int,"dir":"N|E|S|W","drop_ft":int optional}, ...].

    - If a matching drop edge has drop_ft specified, use it (>=0).
    - Otherwise compute drop_ft = max(0, elev(from) - elev(to)).

    If there is no drop edge on that boundary, returns 0.
    """
    try:
        fx = int(from_gx); fy = int(from_gy)
        tx = int(to_gx); ty = int(to_gy)
    except Exception:
        return 0

    dx = tx - fx
    dy = ty - fy
    # Only supports cardinal adjacent transitions.
    if abs(dx) + abs(dy) != 1:
        return 0

    if dx == 1:
        d = "E"
    elif dx == -1:
        d = "W"
    elif dy == 1:
        d = "S"
    else:
        d = "N"

    drop_edges = (meta or {}).get("drop_edges", []) or []
    if not isinstance(drop_edges, list) or not drop_edges:
        return 0

    for de in drop_edges:
        if not isinstance(de, dict):
            continue
        try:
            ex = int(de.get("x"))
            ey = int(de.get("y"))
            ed = str(de.get("dir", de.get("edge", "N"))).upper().strip()
        except Exception:
            continue
        if ex == fx and ey == fy and ed == d:
            # explicit depth wins
            try:
                if "drop_ft" in de and de.get("drop_ft") is not None:
                    return max(0, int(de.get("drop_ft") or 0))
            except Exception:
                pass
            return compute_drop_ft(meta, fx, fy, tx, ty)

    return 0
