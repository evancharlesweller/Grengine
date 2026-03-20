# engine/visibility_engine.py
"""Deterministic visibility / LOS helpers.

Phase B2: True LOS + Fog Upgrade

Rules:
- Vision is radius-capped (feet -> squares) and then filtered by LOS.
- LOS is blocked by edge-walls from map metadata.
- Optionally, "blocked" cells can be treated as opaque (default: True).

This module intentionally contains:
- NO Qt imports
- NO HTTP
- NO disk I/O
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

DIRS = ("N", "E", "S", "W")


def _norm_dir(d: str) -> str:
    d = str(d or "").upper().strip()
    return d if d in DIRS else "N"


def _wall_set(meta: Dict[str, Any]) -> Set[Tuple[int, int, str]]:
    """Return a normalized set of (x,y,dir) walls."""
    out: Set[Tuple[int, int, str]] = set()
    for w in (meta or {}).get("walls", []) or []:
        if not isinstance(w, dict):
            continue
        try:
            x = int(w.get("x"))
            y = int(w.get("y"))
            d = _norm_dir(w.get("dir"))
        except Exception:
            continue
        out.add((x, y, d))
    return out


def _blocked_set(meta: Dict[str, Any]) -> Set[Tuple[int, int]]:
    out: Set[Tuple[int, int]] = set()
    for b in (meta or {}).get("blocked", []) or []:
        try:
            x = int(b[0])
            y = int(b[1])
        except Exception:
            continue
        out.add((x, y))
    return out


def _has_wall_between(a: Tuple[int, int], b: Tuple[int, int], walls: Set[Tuple[int, int, str]]) -> bool:
    """Check if an edge-wall blocks movement/LOS between adjacent cells a and b."""
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay

    if dx == 1 and dy == 0:
        return (ax, ay, "E") in walls or (bx, by, "W") in walls
    if dx == -1 and dy == 0:
        return (ax, ay, "W") in walls or (bx, by, "E") in walls
    if dx == 0 and dy == 1:
        return (ax, ay, "S") in walls or (bx, by, "N") in walls
    if dx == 0 and dy == -1:
        return (ax, ay, "N") in walls or (bx, by, "S") in walls

    # Not adjacent
    return False

def has_line_of_sight(
    origin: Tuple[int, int],
    target: Tuple[int, int],
    meta: Dict[str, Any],
    *,
    treat_blocked_as_opaque: bool = True,
) -> bool:
    """True LOS using grid boundary traversal (DDA / voxel raycast).

    Ray is cast from center of origin cell to center of target cell.
    Each time the ray crosses a cell boundary, we check the corresponding edge-wall.
    Corner hits (crossing X and Y at same time) check BOTH boundaries to prevent corner leaks.
    """
    ox, oy = int(origin[0]), int(origin[1])
    tx, ty = int(target[0]), int(target[1])

    if (ox, oy) == (tx, ty):
        return True

    walls = _wall_set(meta)
    blocked = _blocked_set(meta) if treat_blocked_as_opaque else set()

    # Continuous coordinates: center-to-center ray
    x0 = ox + 0.5
    y0 = oy + 0.5
    x1 = tx + 0.5
    y1 = ty + 0.5

    dx = x1 - x0
    dy = y1 - y0

    # Determine step direction for grid traversal
    step_x = 1 if dx > 0 else (-1 if dx < 0 else 0)
    step_y = 1 if dy > 0 else (-1 if dy < 0 else 0)

    # Avoid division by zero: use "infinite" deltas when axis is flat
    inf = 10**18
    t_delta_x = abs(1.0 / dx) if dx != 0 else inf
    t_delta_y = abs(1.0 / dy) if dy != 0 else inf

    # Current cell (integer grid)
    cx, cy = ox, oy

    # Where along the ray we next cross a vertical/horizontal gridline
    # Compute initial tMax for X
    if step_x > 0:
        next_vx = (cx + 1)  # next vertical grid line to the right
        t_max_x = (next_vx - x0) / dx
    elif step_x < 0:
        next_vx = cx  # next vertical grid line to the left
        t_max_x = (x0 - next_vx) / (-dx)
    else:
        t_max_x = inf

    # Compute initial tMax for Y
    if step_y > 0:
        next_hy = (cy + 1)  # next horizontal grid line downward
        t_max_y = (next_hy - y0) / dy
    elif step_y < 0:
        next_hy = cy  # next horizontal grid line upward
        t_max_y = (y0 - next_hy) / (-dy)
    else:
        t_max_y = inf

    # Small epsilon for float tie comparisons
    eps = 1e-12

    # Walk until we reach target cell
    while (cx, cy) != (tx, ty):
        # Decide which boundary we cross next
        if t_max_x + eps < t_max_y:
            # Cross vertical boundary: (cx,cy) -> (cx+step_x, cy)
            nx, ny = cx + step_x, cy
            if _has_wall_between((cx, cy), (nx, ny), walls):
                return False
            cx, cy = nx, ny
            if treat_blocked_as_opaque and (cx, cy) in blocked and (cx, cy) != (ox, oy):
                return False
            t_max_x += t_delta_x

        elif t_max_y + eps < t_max_x:
            # Cross horizontal boundary: (cx,cy) -> (cx, cy+step_y)
            nx, ny = cx, cy + step_y
            if _has_wall_between((cx, cy), (nx, ny), walls):
                return False
            cx, cy = nx, ny
            if treat_blocked_as_opaque and (cx, cy) in blocked and (cx, cy) != (ox, oy):
                return False
            t_max_y += t_delta_y

        else:
            # Corner hit: crosses BOTH boundaries at same time.
            # Check both edges to prevent corner leaks.
            nx1, ny1 = cx + step_x, cy
            nx2, ny2 = cx, cy + step_y

            if step_x != 0:
                if _has_wall_between((cx, cy), (nx1, ny1), walls):
                    return False
            if step_y != 0:
                if _has_wall_between((cx, cy), (nx2, ny2), walls):
                    return False

            # Move diagonally (both)
            cx += step_x
            cy += step_y
            if treat_blocked_as_opaque and (cx, cy) in blocked and (cx, cy) != (ox, oy):
                return False

            t_max_x += t_delta_x
            t_max_y += t_delta_y

    return True

def _cells_within_radius_circle(cx: int, cy: int, radius: int, cols: int, rows: int) -> Iterable[Tuple[int, int]]:
    r = max(0, int(radius))
    r2 = r * r
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            if dx * dx + dy * dy > r2:
                continue
            x = cx + dx
            y = cy + dy
            if 0 <= x < cols and 0 <= y < rows:
                yield (x, y)


def compute_visible_cells_for_tokens(
    token_cells: Sequence[Tuple[int, int, int]],
    meta: Dict[str, Any],
    cols: int,
    rows: int,
    *,
    treat_blocked_as_opaque: bool = True,
) -> Set[Tuple[int, int]]:
    """Compute union of visible cells for (x,y,vision_radius_cells) tokens."""
    visible: Set[Tuple[int, int]] = set()

    for (cx, cy, radius) in token_cells:
        cx = int(cx)
        cy = int(cy)
        r = max(0, int(radius))
        for cell in _cells_within_radius_circle(cx, cy, r, cols, rows):
            if has_line_of_sight((cx, cy), cell, meta, treat_blocked_as_opaque=treat_blocked_as_opaque):
                visible.add(cell)

    return visible


def compute_player_visible_cells(
    tokens: Dict[str, Any],
    meta: Dict[str, Any],
    cols: int,
    rows: int,
    *,
    feet_per_square: int = 5,
    default_vision_ft: int = 60,
    treat_blocked_as_opaque: bool = True,
) -> Set[Tuple[int, int]]:
    """Convenience helper for EncounterState-like token dicts.

    Uses tokens with side == "player" as vision sources.
    """
    token_cells: List[Tuple[int, int, int]] = []
    fps = max(1, int(feet_per_square))

    for _tid, ts in (tokens or {}).items():
        try:
            side = getattr(ts, "side", "")
        except Exception:
            side = ""
        if str(side) != "player":
            continue

        try:
            cx = int(getattr(ts, "grid_x", 0))
            cy = int(getattr(ts, "grid_y", 0))
        except Exception:
            continue

        try:
            vision_ft = int(getattr(ts, "vision_ft", default_vision_ft) or default_vision_ft)
        except Exception:
            vision_ft = int(default_vision_ft)

        radius = max(0, vision_ft // fps)
        token_cells.append((cx, cy, radius))

    return compute_visible_cells_for_tokens(
        token_cells,
        meta or {},
        int(cols),
        int(rows),
        treat_blocked_as_opaque=treat_blocked_as_opaque,
    )