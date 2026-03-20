# engine/los_engine.py
"""Deterministic LOS checks for targeting policy (Phase B5).

Uses the same occluder segments as visibility/covers:
- edge-walls
- optional blocked-cell perimeters (if included in segment builder)
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple
import math

Point = Tuple[float, float]
Segment = Tuple[Point, Point]

def _seg_intersect(a1: Point, a2: Point, b1: Point, b2: Point) -> bool:
    # Standard 2D segment intersection, inclusive of endpoints.
    def orient(p: Point, q: Point, r: Point) -> float:
        return (q[0]-p[0])*(r[1]-p[1]) - (q[1]-p[1])*(r[0]-p[0])

    def on_seg(p: Point, q: Point, r: Point) -> bool:
        return (min(p[0], r[0]) - 1e-9 <= q[0] <= max(p[0], r[0]) + 1e-9 and
                min(p[1], r[1]) - 1e-9 <= q[1] <= max(p[1], r[1]) + 1e-9)

    o1 = orient(a1, a2, b1)
    o2 = orient(a1, a2, b2)
    o3 = orient(b1, b2, a1)
    o4 = orient(b1, b2, a2)

    # General case
    if (o1 > 0 and o2 < 0 or o1 < 0 and o2 > 0) and (o3 > 0 and o4 < 0 or o3 < 0 and o4 > 0):
        return True

    # Colinear cases
    if abs(o1) <= 1e-9 and on_seg(a1, b1, a2): return True
    if abs(o2) <= 1e-9 and on_seg(a1, b2, a2): return True
    if abs(o3) <= 1e-9 and on_seg(b1, a1, b2): return True
    if abs(o4) <= 1e-9 and on_seg(b1, a2, b2): return True

    return False

def has_los(
    *,
    attacker_grid_x: int,
    attacker_grid_y: int,
    target_grid_x: int,
    target_grid_y: int,
    segments: Sequence[Segment],
    attacker_center: Optional[Point] = None,
    target_center: Optional[Point] = None,
) -> bool:
    """Return True if a straight segment from attacker to target is NOT blocked by any occluder segment."""
    ax = float(attacker_grid_x) + 0.5
    ay = float(attacker_grid_y) + 0.5
    bx = float(target_grid_x) + 0.5
    by = float(target_grid_y) + 0.5

    if attacker_center is not None:
        ax, ay = float(attacker_center[0]), float(attacker_center[1])
    if target_center is not None:
        bx, by = float(target_center[0]), float(target_center[1])

    a1 = (ax, ay)
    a2 = (bx, by)

    # If same cell, LOS is trivially true.
    if abs(ax - bx) < 1e-9 and abs(ay - by) < 1e-9:
        return True

    for (p1, p2) in segments:
        # If occluder touches the target point exactly, we still consider it blocking only if it intersects
        # somewhere along the segment; inclusive intersection is fine for tabletop constraints.
        if _seg_intersect(a1, a2, p1, p2):
            return False
    return True
