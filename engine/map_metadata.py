# engine/map_metadata.py
from __future__ import annotations

import json
import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

DIRS = ("N", "E", "S", "W")

def default_meta(width_cells: int, height_cells: int, grid_px: int) -> Dict[str, Any]:
    return {
        "version": 1,
        "grid_px": int(grid_px),
        "width": int(width_cells),
        "height": int(height_cells),
        "walls": [],          # list[{"x":int,"y":int,"dir":"N|E|S|W"}]
        "half_walls": [],     # list[{"x":int,"y":int,"dir":"N|E|S|W"}] (windows/half-walls)
        "blocked": [],        # list[[x,y]]
        "terrain": {},        # dict["x,y"] -> terrain_id
        "hazards": [],        # list[{"x":int,"y":int,"triggers":[...],"trigger":"enter|turn_start|turn_end", ...}]
        "doors": [],          # list[{"id":str,"x":int,"y":int,"edge":"N|E|S|W","is_open":bool}]
        "lighting": {},       # dict["x,y"] -> "bright|dim|dark|magical_dark"
        "foliage": {},        # dict["x,y"] -> float density [0..1]
        "fog_zones": [],      # list[{"cx":float,"cy":float,"r":float,"density":float}]
        "elevation": {},     # dict["x,y"] -> int feet (verticality)
        "drop_edges": [],   # list[{"x":int,"y":int,"dir":"N|E|S|W","drop_ft":int optional}]
    }

def _wall_key(x: int, y: int, d: str) -> Tuple[int, int, str]:
    d = str(d).upper().strip()
    if d not in DIRS:
        d = "N"
    return (int(x), int(y), d)

def _blocked_key(x: int, y: int) -> Tuple[int, int]:
    return (int(x), int(y))

def _terrain_key(x: int, y: int) -> str:
    return f"{int(x)},{int(y)}"

def validate_meta(meta: Dict[str, Any], width_cells: int, height_cells: int, grid_px: int) -> Dict[str, Any]:
    """Best-effort validation/coercion. Never raises; returns a usable meta dict."""
    try:
        m = dict(meta or {})
    except Exception:
        m = {}

    if int(m.get("version", 0) or 0) <= 0:
        m["version"] = 1

    m["grid_px"] = int(m.get("grid_px", grid_px) or grid_px)
    m["width"] = int(m.get("width", width_cells) or width_cells)
    m["height"] = int(m.get("height", height_cells) or height_cells)

    walls_in = m.get("walls", []) or []
    walls_out: List[Dict[str, Any]] = []
    seen = set()
    for w in walls_in:
        if not isinstance(w, dict):
            continue
        x = w.get("x", None)
        y = w.get("y", None)
        d = w.get("dir", None)
        if x is None or y is None or d is None:
            continue
        k = _wall_key(x, y, d)
        if k in seen:
            continue
        # Allow walls just outside bounds (outer boundary) but clamp cell index
        cx, cy, cd = k
        if cx < 0 or cy < 0 or cx >= m["width"] or cy >= m["height"]:
            continue
        seen.add(k)
        walls_out.append({"x": cx, "y": cy, "dir": cd})
    m["walls"] = walls_out

    blocked_in = m.get("blocked", []) or []
    blocked_out: List[List[int]] = []
    seen_b = set()
    for b in blocked_in:
        if isinstance(b, (list, tuple)) and len(b) == 2:
            x, y = b
        elif isinstance(b, dict):
            x, y = b.get("x"), b.get("y")
        else:
            continue
        if x is None or y is None:
            continue
        k = _blocked_key(x, y)
        if k in seen_b:
            continue
        cx, cy = k
        if cx < 0 or cy < 0 or cx >= m["width"] or cy >= m["height"]:
            continue
        seen_b.add(k)
        blocked_out.append([cx, cy])
    m["blocked"] = blocked_out

    terrain_in = m.get("terrain", {}) or {}
    terrain_out: Dict[str, str] = {}
    if isinstance(terrain_in, dict):
        for k, v in terrain_in.items():
            if not isinstance(k, str):
                continue
            v = "" if v is None else str(v)
            if not v.strip():
                continue
            # key format "x,y"
            try:
                sx, sy = k.split(",")
                cx, cy = int(sx), int(sy)
            except Exception:
                continue
            if cx < 0 or cy < 0 or cx >= m["width"] or cy >= m["height"]:
                continue
            terrain_out[_terrain_key(cx, cy)] = v.strip()
    m["terrain"] = terrain_out

    # ---- Hazards (optional) ----
    hazards_in = m.get("hazards", []) or []
    hazards_out: List[Dict[str, Any]] = []
    if isinstance(hazards_in, list):
        for h in hazards_in:
            if not isinstance(h, dict):
                continue
            try:
                x = int(h.get("x"))
                y = int(h.get("y"))
            except Exception:
                continue
            if x < 0 or y < 0 or x >= int(m["width"]) or y >= int(m["height"]):
                continue

            # Multi-trigger support: normalize list if present; otherwise fall back to single trigger.
            raw_trigs = h.get("triggers", None)
            trigs: List[str] = []
            if isinstance(raw_trigs, list):
                for t0 in raw_trigs:
                    t = str(t0 or "").strip().lower()
                    if t in ("on_enter", "enter", "move", "step"):
                        t = "enter"
                    elif t in ("turnstart", "turn_start", "start"):
                        t = "turn_start"
                    elif t in ("turnend", "turn_end", "end"):
                        t = "turn_end"
                    else:
                        continue
                    if t not in trigs:
                        trigs.append(t)

            trig = str(h.get("trigger", "enter") or "enter").strip().lower()
            if trig in ("on_enter", "enter", "move", "step"):
                trig = "enter"
            elif trig in ("turnstart", "turn_start", "start"):
                trig = "turn_start"
            elif trig in ("turnend", "turn_end", "end"):
                trig = "turn_end"
            else:
                trig = "enter"

            if not trigs:
                trigs = [trig]
            else:
                # Ensure legacy trigger is consistent.
                trig = trigs[0]

            htype = str(h.get("hazard_type", h.get("type", "fire")) or "fire").strip().lower()
            dmg = str(h.get("damage", h.get("damage_expr", "1")) or "1").strip()

            out = dict(h)
            out["x"] = x
            out["y"] = y
            out["hazard_type"] = htype
            out["damage"] = dmg
            out["triggers"] = list(trigs)
            out["trigger"] = trig
            # Optional save metadata for engine-resolved damaging saves.
            save_ability = str(h.get("save_ability", "") or "").strip().upper()
            if save_ability in {"STR", "DEX", "CON", "INT", "WIS", "CHA"}:
                out["save_ability"] = save_ability
            try:
                if h.get("save_dc", None) is not None and str(h.get("save_dc")).strip() != "":
                    out["save_dc"] = int(h.get("save_dc"))
            except Exception:
                pass
            save_on_success = str(h.get("save_on_success", h.get("save_effect", "none")) or "none").strip().lower()
            if save_on_success in {"none", "half", "full"}:
                out["save_on_success"] = save_on_success
            save_mode = str(h.get("save_mode", "normal") or "normal").strip().lower()
            if save_mode in {"normal", "advantage", "disadvantage", "adv", "dis"}:
                out["save_mode"] = save_mode
            hazards_out.append(out)

    m["hazards"] = hazards_out

    # ---- Half-walls / windows ----
    half_in = m.get("half_walls", []) or []
    half_out: List[Dict[str, Any]] = []
    seen_hw = set()
    if isinstance(half_in, list):
        for w in half_in:
            if not isinstance(w, dict):
                continue
            x = w.get("x", None)
            y = w.get("y", None)
            d = w.get("dir", None)
            if x is None or y is None or d is None:
                continue
            k = _wall_key(x, y, d)
            if k in seen_hw:
                continue
            cx, cy, cd = k
            if cx < 0 or cy < 0 or cx >= m["width"] or cy >= m["height"]:
                continue
            seen_hw.add(k)
            half_out.append({"x": cx, "y": cy, "dir": cd})
    m["half_walls"] = half_out

    # ---- Doors ----
    doors_in = m.get("doors", []) or []
    doors_out: List[Dict[str, Any]] = []
    if isinstance(doors_in, list):
        for dd in doors_in:
            if not isinstance(dd, dict):
                continue
            try:
                x = int(dd.get("x"))
                y = int(dd.get("y"))
            except Exception:
                continue
            if x < 0 or y < 0 or x >= int(m["width"]) or y >= int(m["height"]):
                continue
            edge = str(dd.get("edge", dd.get("dir", "N")) or "N").upper().strip()
            if edge not in DIRS:
                edge = "N"
            door_id = str(dd.get("id") or f"D_{x}_{y}_{edge}").strip() or f"D_{x}_{y}_{edge}"
            is_open = bool(dd.get("is_open", False))
            doors_out.append({"id": door_id, "x": x, "y": y, "edge": edge, "is_open": is_open})
    m["doors"] = doors_out

    # ---- Lighting field ----
    lighting_in = m.get("lighting", {}) or {}
    lighting_out: Dict[str, str] = {}
    if isinstance(lighting_in, dict):
        for k, v in lighting_in.items():
            if not isinstance(k, str):
                continue
            try:
                sx, sy = k.split(",")
                cx, cy = int(sx), int(sy)
            except Exception:
                continue
            if cx < 0 or cy < 0 or cx >= m["width"] or cy >= m["height"]:
                continue
            lvl = str(v or "bright").strip().lower()
            if lvl not in {"bright", "dim", "dark", "magical_dark"}:
                lvl = "bright"
            lighting_out[_terrain_key(cx, cy)] = lvl
    m["lighting"] = lighting_out

    # ---- Foliage density ----
    foliage_in = m.get("foliage", {}) or {}
    foliage_out: Dict[str, float] = {}
    if isinstance(foliage_in, dict):
        for k, v in foliage_in.items():
            if not isinstance(k, str):
                continue
            try:
                sx, sy = k.split(",")
                cx, cy = int(sx), int(sy)
            except Exception:
                continue
            if cx < 0 or cy < 0 or cx >= m["width"] or cy >= m["height"]:
                continue
            try:
                dens = float(v)
            except Exception:
                continue
            dens = max(0.0, min(1.0, dens))
            if dens <= 0.0:
                continue
            foliage_out[_terrain_key(cx, cy)] = dens
    m["foliage"] = foliage_out

    # ---- Fog zones ----
    zones_in = m.get("fog_zones", []) or []
    zones_out: List[Dict[str, Any]] = []
    if isinstance(zones_in, list):
        for z in zones_in:
            if not isinstance(z, dict):
                continue
            try:
                cx = float(z.get("cx"))
                cy = float(z.get("cy"))
                rr = float(z.get("r"))
                dens = float(z.get("density", 0.5))
            except Exception:
                continue
            if rr <= 0:
                continue
            dens = max(0.0, min(1.0, dens))
            # allow centers slightly off-grid but keep inside map bounds
            if cx < -1.0 or cy < -1.0 or cx > float(m["width"]) + 1.0 or cy > float(m["height"]) + 1.0:
                continue
            zones_out.append({"cx": cx, "cy": cy, "r": rr, "density": dens})
    m["fog_zones"] = zones_out


    # ---- Elevation field ----
    elev_in = m.get("elevation", {}) or {}
    elev_out: Dict[str, int] = {}
    if isinstance(elev_in, dict):
        for k, v in elev_in.items():
            if not isinstance(k, str):
                continue
            try:
                sx, sy = k.split(",")
                cx, cy = int(sx), int(sy)
            except Exception:
                continue
            if cx < 0 or cy < 0 or cx >= m["width"] or cy >= m["height"]:
                continue
            try:
                ft = int(v or 0)
            except Exception:
                ft = 0
            elev_out[_terrain_key(cx, cy)] = int(ft)
    m["elevation"] = elev_out

    return m

def pick_cell(x_px: float, y_px: float, grid_px: int) -> Tuple[int, int]:
    g = max(1, int(grid_px))
    return (int(x_px // g), int(y_px // g))

def pick_edge(x_px: float, y_px: float, grid_px: int) -> Tuple[int, int, str]:
    """Return (cell_x, cell_y, dir) for nearest edge within the clicked cell."""
    cx, cy = pick_cell(x_px, y_px, grid_px)
    g = max(1, int(grid_px))
    lx = float(x_px) - cx * g
    ly = float(y_px) - cy * g
    dists = {
        "N": ly,
        "S": g - ly,
        "W": lx,
        "E": g - lx,
    }
    d = min(dists, key=dists.get)
    return (cx, cy, d)

def toggle_wall(meta: Dict[str, Any], x: int, y: int, d: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    m = copy.deepcopy(meta or {})
    walls = m.get("walls", []) or []
    key = _wall_key(x, y, d)
    # normalize in-list
    exists_idx = None
    for i, w in enumerate(walls):
        if not isinstance(w, dict):
            continue
        if _wall_key(w.get("x", -999), w.get("y", -999), w.get("dir", "")) == key:
            exists_idx = i
            break
    events: List[Dict[str, Any]] = []
    if exists_idx is not None:
        walls.pop(exists_idx)
        events.append({"type": "map_meta_wall_removed", "x": key[0], "y": key[1], "dir": key[2]})
    else:
        walls.append({"x": key[0], "y": key[1], "dir": key[2]})
        events.append({"type": "map_meta_wall_added", "x": key[0], "y": key[1], "dir": key[2]})
    m["walls"] = walls
    return m, events

def toggle_blocked_cell(meta: Dict[str, Any], x: int, y: int) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    m = copy.deepcopy(meta or {})
    blocked = m.get("blocked", []) or []
    key = _blocked_key(x, y)
    idx = None
    for i, b in enumerate(blocked):
        try:
            bx, by = int(b[0]), int(b[1])
        except Exception:
            continue
        if (bx, by) == key:
            idx = i
            break
    events: List[Dict[str, Any]] = []
    if idx is not None:
        blocked.pop(idx)
        events.append({"type": "map_meta_block_removed", "x": key[0], "y": key[1]})
    else:
        blocked.append([key[0], key[1]])
        events.append({"type": "map_meta_block_added", "x": key[0], "y": key[1]})
    m["blocked"] = blocked
    return m, events

def set_terrain(meta: Dict[str, Any], x: int, y: int, terrain_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    m = copy.deepcopy(meta or {})
    terrain = m.get("terrain", {}) or {}
    if not isinstance(terrain, dict):
        terrain = {}

    tid = "" if terrain_id is None else str(terrain_id).strip()
    k = _terrain_key(x, y)
    events: List[Dict[str, Any]] = []
    if not tid:
        if k in terrain:
            terrain.pop(k, None)
            events.append({"type": "map_meta_terrain_cleared", "x": int(x), "y": int(y)})
    else:
        terrain[k] = tid
        events.append({"type": "map_meta_terrain_set", "x": int(x), "y": int(y), "terrain": tid})
    m["terrain"] = terrain
    return m, events



def _hazard_key(x: int, y: int, trigger: str) -> Tuple[int, int, str]:
    t = str(trigger or "enter").strip().lower()
    if t not in ("enter", "turn_start", "turn_end"):
        t = "enter"
    return (int(x), int(y), t)


def set_hazard(
    meta: Dict[str, Any],
    x: int,
    y: int,
    *,
    hazard_type: str = "fire",
    trigger: str = "enter",
    damage: str = "1",
    save_ability: str = "",
    save_dc: int | None = None,
    save_on_success: str = "none",
    save_mode: str = "normal",
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Upsert a hazard at (x,y) for a specific trigger.
    Hazards live in meta["hazards"] as a list of dicts.
    Uniqueness rule: one hazard per (x,y,trigger).
    """
    m = copy.deepcopy(meta or {})
    hazards = m.get("hazards", []) or []
    if not isinstance(hazards, list):
        hazards = []

    key = _hazard_key(x, y, trigger)
    idx = None
    for i, h in enumerate(hazards):
        if not isinstance(h, dict):
            continue
        try:
            hx = int(h.get("x"))
            hy = int(h.get("y"))
            ht = str(h.get("trigger", "enter") or "enter").strip().lower()
        except Exception:
            continue
        if (hx, hy, ht) == key:
            idx = i
            break

    htype = str(hazard_type or "fire").strip().lower()
    trig = str(key[2])
    dmg = str(damage or "1").strip()

    evs: List[Dict[str, Any]] = []
    new_h = {"x": int(x), "y": int(y), "hazard_type": htype, "trigger": trig, "damage": dmg}
    ab = str(save_ability or "").strip().upper()
    if ab in {"STR", "DEX", "CON", "INT", "WIS", "CHA"}:
        new_h["save_ability"] = ab
    try:
        if save_dc is not None:
            new_h["save_dc"] = int(save_dc)
    except Exception:
        pass
    sos = str(save_on_success or "none").strip().lower()
    if sos in {"none", "half", "full"}:
        new_h["save_on_success"] = sos
    smode = str(save_mode or "normal").strip().lower()
    if smode in {"normal", "advantage", "disadvantage", "adv", "dis"}:
        new_h["save_mode"] = smode

    if idx is None:
        hazards.append(new_h)
        evs.append({"type": "map_meta_hazard_added", **new_h})
    else:
        hazards[idx] = new_h
        evs.append({"type": "map_meta_hazard_set", **new_h})

    m["hazards"] = hazards
    return m, evs


def clear_hazard(meta: Dict[str, Any], x: int, y: int, *, trigger: str = "enter") -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    m = copy.deepcopy(meta or {})
    hazards = m.get("hazards", []) or []
    if not isinstance(hazards, list):
        hazards = []

    key = _hazard_key(x, y, trigger)
    out = []
    removed = False
    for h in hazards:
        if not isinstance(h, dict):
            continue
        try:
            hx = int(h.get("x"))
            hy = int(h.get("y"))
            ht = str(h.get("trigger", "enter") or "enter").strip().lower()
        except Exception:
            continue
        if (hx, hy, ht) == key:
            removed = True
            continue
        out.append(h)

    m["hazards"] = out
    evs: List[Dict[str, Any]] = []
    if removed:
        evs.append({"type": "map_meta_hazard_removed", "x": int(x), "y": int(y), "trigger": str(key[2])})
    return m, evs


# ----------------------------
# Doors (BX2)
# ----------------------------

def _door_key(x: int, y: int, edge: str) -> Tuple[int, int, str]:
    edge = str(edge or "N").upper().strip()
    if edge not in DIRS:
        edge = "N"
    return (int(x), int(y), edge)


def door_at(meta: Dict[str, Any], x: int, y: int, edge: str) -> Optional[Dict[str, Any]]:
    doors = (meta or {}).get("doors", []) or []
    if not isinstance(doors, list):
        return None
    key = _door_key(x, y, edge)
    for d in doors:
        if not isinstance(d, dict):
            continue
        try:
            if _door_key(d.get("x"), d.get("y"), d.get("edge", d.get("dir", "N"))) == key:
                return d
        except Exception:
            continue
    return None


def toggle_door(meta: Dict[str, Any], x: int, y: int, edge: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Toggle presence of a door segment on an edge. Default is closed (is_open=False)."""
    m = copy.deepcopy(meta or {})
    doors = m.get("doors", [])
    if not isinstance(doors, list):
        doors = []
    key = _door_key(x, y, edge)

    out: List[Dict[str, Any]] = []
    removed = False
    existing_id = None
    for d in doors:
        if not isinstance(d, dict):
            continue
        try:
            if _door_key(d.get("x"), d.get("y"), d.get("edge", d.get("dir", "N"))) == key:
                removed = True
                existing_id = d.get("id")
                continue
        except Exception:
            pass
        out.append(d)

    evs: List[Dict[str, Any]] = []
    if removed:
        m["doors"] = out
        evs.append({"type": "map_meta_door_removed", "x": int(x), "y": int(y), "edge": str(key[2])})
        return m, evs

    door_id = f"D_{key[0]}_{key[1]}_{key[2]}"
    out.append({"id": door_id, "x": int(key[0]), "y": int(key[1]), "edge": str(key[2]), "is_open": False})
    m["doors"] = out
    evs.append({"type": "map_meta_door_added", "id": door_id, "x": int(key[0]), "y": int(key[1]), "edge": str(key[2]), "is_open": False})
    return m, evs


def clear_door(meta: Dict[str, Any], x: int, y: int, edge: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Remove door segment if present."""
    m = copy.deepcopy(meta or {})
    doors = m.get("doors", [])
    if not isinstance(doors, list):
        doors = []
    key = _door_key(x, y, edge)
    out: List[Dict[str, Any]] = []
    removed = False
    for d in doors:
        if not isinstance(d, dict):
            continue
        try:
            if _door_key(d.get("x"), d.get("y"), d.get("edge", d.get("dir", "N"))) == key:
                removed = True
                continue
        except Exception:
            pass
        out.append(d)
    m["doors"] = out
    evs: List[Dict[str, Any]] = []
    if removed:
        evs.append({"type": "map_meta_door_removed", "x": int(x), "y": int(y), "edge": str(key[2])})
    return m, evs


def hazards_at(meta: Dict[str, Any], x: int, y: int, *, trigger: str = "") -> List[Dict[str, Any]]:
    hazards = (meta or {}).get("hazards", []) or []
    if not isinstance(hazards, list):
        return []
    trig = str(trigger or "").strip().lower()
    out = []
    for h in hazards:
        if not isinstance(h, dict):
            continue
        try:
            hx = int(h.get("x"))
            hy = int(h.get("y"))
        except Exception:
            continue
        if hx != int(x) or hy != int(y):
            continue
        if trig:
            ht = str(h.get("trigger", "enter") or "enter").strip().lower()
            if ht != trig:
                continue
        out.append(h)
    return out
def load_meta_file(meta_path: str, width_cells: int, height_cells: int, grid_px: int) -> Dict[str, Any]:
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return default_meta(width_cells, height_cells, grid_px)
    except Exception:
        return default_meta(width_cells, height_cells, grid_px)

    return validate_meta(raw, width_cells, height_cells, grid_px)

def save_meta_file(meta_path: str, meta: Dict[str, Any]) -> None:
    # Best effort; never raise to UI.
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
    except Exception:
        return