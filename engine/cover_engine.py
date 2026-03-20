from __future__ import annotations

from typing import Any, Dict, List, Literal, Tuple, Optional

from .visibility_polygon_engine import build_segments_from_meta

Point = Tuple[float, float]
Segment = Tuple[Point, Point]
CoverTier = Literal["none", "half", "three_quarters", "total"]


def _token_sample_points(grid_x: int, grid_y: int) -> List[Point]:
    """Return deterministic sample points for a 1x1 token in grid-space.

    Grid-space means each cell is 1x1 and token occupies [x,x+1] x [y,y+1].
    We sample the center + 4 near-corners (not exactly on edges) to reduce
    degeneracy when rays align perfectly with wall edges.
    """
    x = float(grid_x)
    y = float(grid_y)
    # small inset from the cell corners
    inset = 0.12
    return [
        (x + 0.5, y + 0.5),  # center
        (x + inset, y + inset),  # NW-ish (top-left)
        (x + 1.0 - inset, y + inset),  # NE-ish
        (x + inset, y + 1.0 - inset),  # SW-ish
        (x + 1.0 - inset, y + 1.0 - inset),  # SE-ish
    ]


def _orient(a: Point, b: Point, c: Point) -> float:
    # Cross product (b-a) x (c-a)
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Point, b: Point, p: Point, eps: float = 1e-9) -> bool:
    return (
        min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
        and min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps
    )


def _segments_intersect(s1: Segment, s2: Segment, eps: float = 1e-9) -> bool:
    """Robust-ish segment intersection test.

    We treat touching at endpoints as intersection (blocks ray) unless the
    contact is *very* close to the ray origin or ray target (handled upstream).
    """
    (p1, p2) = s1
    (q1, q2) = s2

    o1 = _orient(p1, p2, q1)
    o2 = _orient(p1, p2, q2)
    o3 = _orient(q1, q2, p1)
    o4 = _orient(q1, q2, p2)

    # General case
    if (o1 > eps and o2 < -eps) or (o1 < -eps and o2 > eps):
        if (o3 > eps and o4 < -eps) or (o3 < -eps and o4 > eps):
            return True

    # Colinear / touching cases
    if abs(o1) <= eps and _on_segment(p1, p2, q1, eps):
        return True
    if abs(o2) <= eps and _on_segment(p1, p2, q2, eps):
        return True
    if abs(o3) <= eps and _on_segment(q1, q2, p1, eps):
        return True
    if abs(o4) <= eps and _on_segment(q1, q2, p2, eps):
        return True

    return False


def _ray_blocked(origin: Point, target: Point, segs: List[Segment]) -> bool:
    """Return True if line segment origin->target intersects any occluder segment."""
    ray = (origin, target)

    # Ignore intersections extremely close to endpoints to reduce 'self-hit' on
    # adjacent walls and corner caps.
    ox, oy = origin
    tx, ty = target
    for s in segs:
        if not _segments_intersect(ray, s, eps=1e-9):
            continue

        # Compute a cheap 'distance to endpoints' filter for near-endpoint touches.
        # We only need to suppress cases where the ray starts/ends exactly on a segment.
        (a, b) = s
        for px, py in (a, b):
            # If the intersection is basically at ray origin/target endpoints, ignore.
            if (px - ox) ** 2 + (py - oy) ** 2 < 1e-6:
                continue
            if (px - tx) ** 2 + (py - ty) ** 2 < 1e-6:
                continue

        return True

    return False


def compute_cover(
    *,
    attacker_grid_x: int,
    attacker_grid_y: int,
    target_grid_x: int,
    target_grid_y: int,
    meta: Dict[str, Any],
    door_state: Optional[Dict[str, bool]] = None,
) -> Tuple[CoverTier, int, Dict[str, Any]]:
    """Compute cover tier from attacker to target using geometry occluders.

    Returns: (tier, ac_bonus, debug)

    Tiers map to D&D5e defaults:
      - none: +0 AC
      - half: +2 AC
      - three_quarters: +5 AC
      - total: cannot target (represented by ac_bonus=0; caller should block)
    """
    segs = build_segments_from_meta(meta or {}, include_blocked=True, include_half_walls=False, door_state=door_state)

    origin = (float(attacker_grid_x) + 0.5, float(attacker_grid_y) + 0.5)
    samples = _token_sample_points(int(target_grid_x), int(target_grid_y))

    blocked = 0
    per_sample = []
    for pt in samples:
        is_blocked = _ray_blocked(origin, pt, segs)
        per_sample.append({"pt": pt, "blocked": bool(is_blocked)})
        if is_blocked:
            blocked += 1

    # 5 rays: center + 4 corners
    if blocked <= 0:
        tier: CoverTier = "none"
        bonus = 0
    elif blocked <= 2:
        tier = "half"
        bonus = 2
    elif blocked <= 4:
        tier = "three_quarters"
        bonus = 5
    else:
        tier = "total"
        bonus = 0

    
    # If no cover from full occluders, allow half-walls/windows to grant HALF cover (capped at half).
    if tier == "none":
        half_segs = build_segments_from_meta(meta or {}, include_blocked=False, include_half_walls=True, door_state=door_state)
        # remove full occluder segments from half list if any (half_segs currently includes half-walls only, plus corner caps/dedupe)
        origin_hw = origin
        blocked_hw = 0
        for pt in samples:
            if _ray_blocked(origin_hw, pt, half_segs):
                blocked_hw += 1
        if blocked_hw > 0:
            tier = "half"
            bonus = 2

    debug = {
        "blocked_rays": blocked,
        "total_rays": len(samples),
        "samples": per_sample,
    }
    return tier, bonus, debug


_TIER_RANK = {"none": 0, "half": 1, "three_quarters": 2, "total": 3}

def merge_cover_tiers(computed: CoverTier, override: CoverTier) -> CoverTier:
    """Return the more-protective of the two tiers."""
    try:
        if _TIER_RANK.get(str(override), 0) > _TIER_RANK.get(str(computed), 0):
            return override  # type: ignore
    except Exception:
        pass
    return computed

def cover_bonus_for_tier(tier: CoverTier) -> int:
    if tier == "half":
        return 2
    if tier == "three_quarters":
        return 5
    return 0
