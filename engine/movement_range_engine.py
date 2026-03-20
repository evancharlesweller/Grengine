# engine/movement_range_engine.py
"""Deterministic movement reachability with metadata integration (Phase B4).

- No Qt imports
- No IO / HTTP
- Terrain costs follow D&D-style difficult terrain (2x)
- Blocked cells are impassable for reachability computations
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
import heapq

Coord = Tuple[int, int]

_DEFAULT_DIFFICULT_IDS = {
    "difficult",
    "uneven",
    "mud",
    "sand",
    "water",
    "ice",
    "hazard",
}

def _terrain_id_at(meta: Dict[str, Any], x: int, y: int) -> str:
    terrain = (meta or {}).get("terrain", {}) or {}
    try:
        return str(terrain.get(f"{int(x)},{int(y)}", "") or "").strip().lower()
    except Exception:
        return ""

def is_blocked(meta: Dict[str, Any], x: int, y: int) -> bool:
    blocked = (meta or {}).get("blocked", []) or []
    try:
        xi, yi = int(x), int(y)
    except Exception:
        return False
    for b in blocked:
        try:
            if int(b[0]) == xi and int(b[1]) == yi:
                return True
        except Exception:
            continue
    return False

def cell_cost_squares(meta: Dict[str, Any], x: int, y: int, *, difficult_ids: Optional[Set[str]] = None) -> int:
    """Return movement cost to ENTER cell (x,y) measured in squares (1 or 2)."""
    tid = _terrain_id_at(meta, x, y)
    if difficult_ids is None:
        difficult_ids = _DEFAULT_DIFFICULT_IDS
    return 2 if tid in difficult_ids else 1

def neighbors4(x: int, y: int) -> List[Coord]:
    return [(x+1, y), (x-1, y), (x, y+1), (x, y-1)]

def compute_reachable_cells(
    *,
    start_x: int,
    start_y: int,
    move_ft: int,
    cols: int,
    rows: int,
    meta: Dict[str, Any],
    feet_per_square: int = 5,
    allow_diagonal: bool = False,
) -> Set[Coord]:
    """Compute all reachable cells within move_ft.

    Uses Dijkstra over the grid with per-cell entry costs.
    Blocked cells are excluded entirely.

    Returns set of (x,y) including start cell.
    """
    fps = max(1, int(feet_per_square))
    budget_sq = max(0, int(move_ft) // fps)

    sx, sy = int(start_x), int(start_y)

    if sx < 0 or sy < 0 or sx >= int(cols) or sy >= int(rows):
        return set()

    if is_blocked(meta, sx, sy):
        # If token is somehow in a blocked cell, still allow "standing cell" reachability.
        return {(sx, sy)}

    dist: Dict[Coord, int] = {(sx, sy): 0}
    pq: List[Tuple[int, int, int]] = [(0, sx, sy)]
    reachable: Set[Coord] = {(sx, sy)}

    while pq:
        cost, x, y = heapq.heappop(pq)
        if cost != dist.get((x, y), None):
            continue
        if cost > budget_sq:
            continue

        nbrs = neighbors4(x, y)
        if allow_diagonal:
            nbrs += [(x+1,y+1),(x+1,y-1),(x-1,y+1),(x-1,y-1)]

        for nx, ny in nbrs:
            if nx < 0 or ny < 0 or nx >= int(cols) or ny >= int(rows):
                continue
            if is_blocked(meta, nx, ny):
                continue
            step = cell_cost_squares(meta, nx, ny)
            ncost = cost + int(step)
            if ncost > budget_sq:
                continue
            if ncost < dist.get((nx, ny), 1_000_000_000):
                dist[(nx, ny)] = ncost
                heapq.heappush(pq, (ncost, nx, ny))
                reachable.add((nx, ny))

    return reachable

def compute_min_cost_ft(
    *,
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
    cols: int,
    rows: int,
    meta: Dict[str, Any],
    feet_per_square: int = 5,
) -> Optional[int]:
    """Return minimal movement cost in feet from (from_x,from_y) to (to_x,to_y).
    Returns None if unreachable (e.g., blocked destination or disconnected).
    """
    fps = max(1, int(feet_per_square))
    fx, fy = int(from_x), int(from_y)
    tx, ty = int(to_x), int(to_y)

    if tx < 0 or ty < 0 or tx >= int(cols) or ty >= int(rows):
        return None
    if is_blocked(meta, tx, ty):
        return None
    if fx < 0 or fy < 0 or fx >= int(cols) or fy >= int(rows):
        return None

    # Dijkstra until we hit target; early exit.
    dist: Dict[Coord, int] = {(fx, fy): 0}
    pq: List[Tuple[int, int, int]] = [(0, fx, fy)]

    while pq:
        cost, x, y = heapq.heappop(pq)
        if cost != dist.get((x, y), None):
            continue
        if (x, y) == (tx, ty):
            return int(cost) * fps

        for nx, ny in neighbors4(x, y):
            if nx < 0 or ny < 0 or nx >= int(cols) or ny >= int(rows):
                continue
            if is_blocked(meta, nx, ny):
                continue
            step = cell_cost_squares(meta, nx, ny)
            ncost = cost + int(step)
            if ncost < dist.get((nx, ny), 1_000_000_000):
                dist[(nx, ny)] = ncost
                heapq.heappush(pq, (ncost, nx, ny))

    return None
