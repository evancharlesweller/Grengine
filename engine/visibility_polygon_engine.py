# engine/visibility_polygon_engine.py
"""
Continuous visibility polygon engine (Phase B2+ Fog Redux)

- Deterministic (no Qt, no IO, no HTTP)
- Uses wall segments derived from map metadata (edge-walls).
- Optionally treats blocked cells as opaque by adding their perimeter as segments.
- Computes visibility polygon by angular raycasting to segment endpoints (+/- eps).

Coordinate system:
- Grid units (cells). Cell corners are integer coordinates.
- Token origin is typically (grid_x + 0.5, grid_y + 0.5).
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Sequence, Tuple, Optional

Point = Tuple[float, float]
Segment = Tuple[Point, Point]


def _canon_edge(x: int, y: int, d: str) -> Tuple[int, int, str]:
    """Canonicalize an edge so each physical edge has a single representation.
    We store edges as (x,y,'N') or (x,y,'W') where:
      - 'S' becomes (x, y+1, 'N')
      - 'E' becomes (x+1, y, 'W')
    """
    d = str(d or "N").upper()
    if d == "S":
        return (x, y + 1, "N")
    if d == "E":
        return (x + 1, y, "W")
    if d == "N":
        return (x, y, "N")
    # W
    return (x, y, "W")
def _foliage_density_at(meta: Dict[str, Any], x: int, y: int) -> float:
    foliage = (meta or {}).get("foliage", {}) or {}
    try:
        v = foliage.get(f"{int(x)},{int(y)}", 0.0)
        f = float(v)
    except Exception:
        f = 0.0
    if f < 0.0:
        f = 0.0
    if f > 1.0:
        f = 1.0
    return f

def _fog_density_at(meta: Dict[str, Any], px: float, py: float) -> float:
    # fog_zones: list[{"cx":float,"cy":float,"r":float,"density":float}]
    zones = (meta or {}).get("fog_zones", []) or []
    total = 0.0
    for z in zones:
        if not isinstance(z, dict):
            continue
        try:
            cx = float(z.get("cx", 0.0))
            cy = float(z.get("cy", 0.0))
            r = float(z.get("r", 0.0))
            d = float(z.get("density", 0.0))
        except Exception:
            continue
        if r <= 0.0 or d <= 0.0:
            continue
        dx = px - cx
        dy = py - cy
        if (dx*dx + dy*dy) <= (r*r):
            total += max(0.0, min(1.0, d))
    if total > 1.0:
        total = 1.0
    return total

def make_medium_attenuator(meta: Dict[str, Any], *, step_cells: float = 0.25, budget: float = 1.0):
    """Return an attenuator(origin, dir, max_t)->cap_t implementing soft vision blocking.

    Model:
      attenuation += density * step_cells
      stop when attenuation >= budget

    - foliage density comes from meta["foliage"]["x,y"] in [0..1]
      (treat 1.0 as fully opaque after ~1 cell).
    - fog zones add circular density (useful for gas/smoke clouds).
    """
    step = max(0.05, float(step_cells))
    bud = max(0.01, float(budget))

    def _atten(origin: Point, direction: Point, max_t: float) -> float:
        ox, oy = float(origin[0]), float(origin[1])
        ux, uy = float(direction[0]), float(direction[1])
        mt = max(0.0, float(max_t))

        t = 0.0
        acc = 0.0
        # march forward in grid-space
        while t < mt:
            t_next = min(mt, t + step)
            mid = 0.5 * (t + t_next)
            px = ox + mid * ux
            py = oy + mid * uy

            # Fog density uses continuous space
            dens = _fog_density_at(meta, px, py)

            # Foliage density uses cell lookup
            cx = int(px // 1.0)
            cy = int(py // 1.0)
            dens += _foliage_density_at(meta, cx, cy)

            if dens > 1.0:
                dens = 1.0
            if dens > 0.0:
                acc += dens * (t_next - t)
                if acc >= bud:
                    return t  # stop before entering this step
            t = t_next
        return mt

    return _atten


def make_lighting_attenuator(
    meta: Dict[str, Any],
    attacker_ts: Any,
    *,
    feet_per_square: int = 5,
    step_cells: float = 0.20,
):
    """Return an attenuator(origin, dir, max_t)->cap_t that enforces lighting perception.

    This is used for fog/visibility rendering, not for LOS policy (B5).

    Approach:
      - March forward along the ray in small steps (grid-space).
      - Find the first point that lands in a cell that the attacker cannot perceive
        under the current lighting + vision type.
      - Cap the ray just before entering that step.

    Notes:
      - This yields continuous polygon edges, while still respecting cell-authored
        lighting fields (bright/dim/dark/magical_dark).
      - Deterministic: no randomness, no IO.
    """
    step = max(0.05, float(step_cells))
    fps = max(1, int(feet_per_square))

    # Local import to avoid hard dependency/cycle.
    try:
        from engine.perception_engine import can_perceive_cell
    except Exception:
        can_perceive_cell = None

    if can_perceive_cell is None:
        # If perception layer is absent, do not cap.
        def _noop(_origin: Point, _direction: Point, max_t: float) -> float:
            return float(max_t)

        return _noop

    def _atten(origin: Point, direction: Point, max_t: float) -> float:
        ox, oy = float(origin[0]), float(origin[1])
        ux, uy = float(direction[0]), float(direction[1])
        mt = max(0.0, float(max_t))

        t = 0.0
        while t < mt:
            t_next = min(mt, t + step)
            mid = 0.5 * (t + t_next)
            px = ox + mid * ux
            py = oy + mid * uy

            gx = int(px // 1.0)
            gy = int(py // 1.0)
            try:
                pr = can_perceive_cell(attacker_ts=attacker_ts, gx=gx, gy=gy, meta=meta, feet_per_square=fps)
                if not bool(getattr(pr, "can_perceive", False)):
                    return t  # stop before entering this step
            except Exception:
                # Fail-open if perception throws; do not hide cells.
                return mt

            t = t_next
        return mt

    return _atten


def _wall_set(meta: Dict[str, Any]) -> set[tuple[int, int, str]]:
    out: set[tuple[int, int, str]] = set()
    for w in (meta or {}).get("walls", []) or []:
        if not isinstance(w, dict):
            continue
        try:
            x = int(w.get("x"))
            y = int(w.get("y"))
            d = str(w.get("dir", w.get("edge", "N"))).upper().strip()
        except Exception:
            continue
        if d not in ("N", "S", "E", "W"):
            continue
        out.add(_canon_edge(x, y, d))
    return out



def _half_wall_set(meta: Dict[str, Any]) -> set[tuple[int, int, str]]:
    """Return canonical set of (x,y,dir) for half-walls/windows.

    Accepts either {x,y,dir} or {x,y,edge}.
    Canonicalizes to N/W representation (see _canon_edge).
    """
    out: set[tuple[int, int, str]] = set()
    for w in (meta or {}).get("half_walls", []) or []:
        if not isinstance(w, dict):
            continue
        try:
            x = int(w.get("x"))
            y = int(w.get("y"))
            d = str(w.get("dir", w.get("edge", "N"))).upper().strip()
        except Exception:
            continue
        if d not in ("N", "S", "E", "W"):
            continue
        out.add(_canon_edge(x, y, d))
    return out



def _closed_door_set(meta: Dict[str, Any], door_state: Optional[Dict[str, bool]] = None) -> set[tuple[int, int, str]]:
    """Return canonical set of door edges that are CLOSED (i.e., act as occluders)."""
    out: set[tuple[int, int, str]] = set()
    ds = door_state or {}
    for dd in (meta or {}).get("doors", []) or []:
        if not isinstance(dd, dict):
            continue
        try:
            door_id = str(dd.get("id") or "").strip()
            x = int(dd.get("x"))
            y = int(dd.get("y"))
            d = str(dd.get("edge", dd.get("dir", "N"))).upper().strip()
        except Exception:
            continue
        if d not in ("N", "S", "E", "W"):
            continue

        default_open = bool(dd.get("is_open", False))
        is_open = bool(ds.get(door_id, default_open))
        if not is_open:
            out.add(_canon_edge(x, y, d))
    return out



def _door_edge_set(meta: Dict[str, Any]) -> set[tuple[int, int, str]]:
    """Return canonical set of (x,y,dir) for all door edges (open or closed).

    Accepts {x,y,edge} (preferred) and {x,y,dir} (legacy).
    Canonicalizes to N/W representation (see _canon_edge) so we can reliably
    compare against wall edges even if authored using S/E equivalents.
    """
    out: set[tuple[int, int, str]] = set()
    for dd in (meta or {}).get("doors", []) or []:
        if not isinstance(dd, dict):
            continue
        try:
            x = int(dd.get("x"))
            y = int(dd.get("y"))
            d = str(dd.get("edge", dd.get("dir", "N"))).upper().strip()
        except Exception:
            continue
        if d not in ("N", "S", "E", "W"):
            continue
        out.add(_canon_edge(x, y, d))
    return out

    for d0 in doors:
        if not isinstance(d0, dict):
            continue
        try:
            x = int(d0.get("x"))
            y = int(d0.get("y"))
            edge = str(d0.get("edge", d0.get("dir", "N")) or "N").upper().strip()
        except Exception:
            continue
        if edge not in ("N", "E", "S", "W"):
            continue
        out.add((x, y, edge))
    return out



def _blocked_set(meta: Dict[str, Any]) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for b in (meta or {}).get("blocked", []) or []:
        try:
            out.add((int(b[0]), int(b[1])))
        except Exception:
            continue
    return out

def _add_corner_caps(segs: List[Segment], eps: float = 1e-3) -> List[Segment]:
    """
    Add tiny "cap" segments at vertices where multiple segments meet.
    This seals numerical pinholes at perfect corners (prevents corner vision leaks).

    eps is in grid units (cells). 1e-3 is 0.001 of a cell, visually invisible.
    """
    if not segs:
        return segs

    # Count how many segment endpoints touch each vertex
    counts: Dict[Tuple[float, float], int] = {}
    for (a, b) in segs:
        ax, ay = float(a[0]), float(a[1])
        bx, by = float(b[0]), float(b[1])
        counts[(ax, ay)] = counts.get((ax, ay), 0) + 1
        counts[(bx, by)] = counts.get((bx, by), 0) + 1

    caps: List[Segment] = []
    for (vx, vy), c in counts.items():
        # Only cap "junction" vertices (corners, tees, crosses)
        if c < 2:
            continue

        # Horizontal and vertical micro-segments centered on the vertex
        caps.append(((vx - eps, vy), (vx + eps, vy)))
        caps.append(((vx, vy - eps), (vx, vy + eps)))

    return segs + caps

def build_segments_from_meta(
    meta: Dict[str, Any],
    *,
    include_blocked: bool = True,
    include_half_walls: bool = False,
    door_state: Optional[Dict[str, bool]] = None,
) -> List[Segment]:
    """Convert edge-walls and (optional) blocked cells into line segments in grid-space."""
    segs: List[Segment] = []
    walls = _wall_set(meta)
    # BX2: doors are authoritative edges. If a door exists on an edge, any wall edge on that same edge is ignored.
    door_edges_all = _door_edge_set(meta)
    if door_edges_all:
        walls = set(walls) - set(door_edges_all)
    # Doors contribute occluder segments only when CLOSED.
    walls = set(walls) | set(_closed_door_set(meta, door_state))

    # Edge walls: each is one segment along a cell edge
    for (x, y, d) in walls:
        if d == "N":
            segs.append(((x, y), (x + 1, y)))
        elif d == "S":
            segs.append(((x, y + 1), (x + 1, y + 1)))
        elif d == "W":
            segs.append(((x, y), (x, y + 1)))
        elif d == "E":
            segs.append(((x + 1, y), (x + 1, y + 1)))

    # Blocked cells: add their perimeter as opaque segments (optional)
    if include_blocked:
        for (bx, by) in _blocked_set(meta):
            # perimeter of cell [bx,bx+1] x [by,by+1]
            segs.append(((bx, by), (bx + 1, by)))         # N
            segs.append(((bx, by + 1), (bx + 1, by + 1))) # S
            segs.append(((bx, by), (bx, by + 1)))         # W
            segs.append(((bx + 1, by), (bx + 1, by + 1))) # E

    # Seal corner pinholes deterministically
    segs = _dedupe_segments(segs)
    segs = _add_corner_caps(segs, eps=2e-3)
    return _dedupe_segments(segs)


def _dedupe_segments(segs: Sequence[Segment]) -> List[Segment]:
    """Remove exact duplicates (and reversed duplicates) deterministically."""
    seen: set[tuple[float, float, float, float]] = set()
    out: List[Segment] = []
    for (a, b) in segs:
        ax, ay = float(a[0]), float(a[1])
        bx, by = float(b[0]), float(b[1])
        k1 = (ax, ay, bx, by)
        k2 = (bx, by, ax, ay)
        if k1 in seen or k2 in seen:
            continue
        seen.add(k1)
        out.append(((ax, ay), (bx, by)))
    return out


def _segment_intersect_ray(origin: Point, direction: Point, seg: Segment) -> Tuple[bool, float, Point]:
    """
    Ray/segment intersection in 2D.
    Ray: origin + t*direction, t >= 0
    Segment: p + u*(q-p), u in [0,1]
    Returns (hit, t, point). t is distance along the ray direction vector scale.
    """
    (ox, oy) = origin
    (dx, dy) = direction
    (p, q) = seg
    (px, py) = p
    (qx, qy) = q

    sx = qx - px
    sy = qy - py

    # Solve:
    # origin + t*d = p + u*s
    # using cross products
    denom = dx * sy - dy * sx
    if abs(denom) < 1e-12:
        return (False, 0.0, (0.0, 0.0))  # parallel

    rx = px - ox
    ry = py - oy

    t = (rx * sy - ry * sx) / denom
    u = (rx * dy - ry * dx) / denom

    if t < 0.0:
        return (False, 0.0, (0.0, 0.0))
    if u < 0.0 or u > 1.0:
        return (False, 0.0, (0.0, 0.0))
    
    ix = ox + t * dx
    iy = oy + t * dy

    # --- Corner leak guard ---
    # If the intersection is extremely close to a segment endpoint, we can get
    # "grazing" rays that slip through a perfect corner. Bias the hit slightly
    # forward along the ray so the corner behaves like a closed seam.
    #
    # This does NOT change determinism, only stabilizes corner behavior.
    ex1, ey1 = px, py
    ex2, ey2 = qx, qy
    if (abs(ix - ex1) <= 1e-6 and abs(iy - ey1) <= 1e-6) or (abs(ix - ex2) <= 1e-6 and abs(iy - ey2) <= 1e-6):
        t = t + 1e-6
        ix = ox + t * dx
        iy = oy + t * dy

    return (True, t, (ix, iy))


def _ray_circle_intersection(origin: Point, direction: Point, radius: float) -> float | None:
    """
    Closest positive t where ray hits circle centered at origin with given radius.
    direction should be unit-length for t to be distance in grid units.
    """
    # Ray from origin: O + t*D, circle centered at O -> |tD| = r => t = r
    # Because the circle is centered at origin, the intersection along the ray is exactly radius.
    if radius <= 0:
        return None
    return float(radius)


def compute_visibility_polygon(
    origin_xy: Point,
    segments: Sequence[Segment],
    radius: float,
    *,
    eps_angle: float = 1e-3,
    attenuator=None,
) -> List[Point]:
    """
    Compute a visibility polygon around origin with radius cap.
    Returns points (x,y) in grid-space in CCW angle order.

    CRITICAL FIX:
    - Normalize *all* angles into [0, 2π) before sorting.
      Mixing atan2 (-π..π) with baseline rays (0..2π) causes wrap discontinuities,
      which produce the "bottom-left corner leak" / diagonal wedge artifacts.
    """
    ox, oy = float(origin_xy[0]), float(origin_xy[1])
    r = max(0.0, float(radius))

    if r <= 1e-9:
        return [(ox, oy)]

    TWO_PI = 2.0 * math.pi

    def _norm_angle(a: float) -> float:
        # Normalize to [0, 2π)
        a = a % TWO_PI
        # Guard against 2π floating representation
        if a >= TWO_PI:
            a -= TWO_PI
        return a

    # collect unique endpoints
    endpoints: List[Point] = []
    seen_ep: set[tuple[float, float]] = set()
    for (a, b) in segments:
        for p in (a, b):
            k = (float(p[0]), float(p[1]))
            if k in seen_ep:
                continue
            seen_ep.add(k)
            endpoints.append(k)

    angles: List[float] = []

    # Angles to cast (endpoint +/- eps)
    for (ex, ey) in endpoints:
        ang = math.atan2(ey - oy, ex - ox)
        angles.append(_norm_angle(ang - eps_angle))
        angles.append(_norm_angle(ang))
        angles.append(_norm_angle(ang + eps_angle))

    # Baseline rays for smoothness / stability in open areas
    BASE_RAYS = 360
    for i in range(BASE_RAYS):
        angles.append(_norm_angle(TWO_PI * (i / BASE_RAYS)))

    # Deduplicate + sort
    angles = sorted(set(angles))

    hits: List[Tuple[float, Point]] = []

    for ang in angles:
        ux = math.cos(ang)
        uy = math.sin(ang)

        # nearest intersection defaults to radius circle
        best_t = _ray_circle_intersection((ox, oy), (ux, uy), r)
        if best_t is None:
            best_t = r

        # Medium attenuation cap (fog/foliage), if provided.
        try:
            if callable(attenuator):
                cap_t = attenuator((ox, oy), (ux, uy), float(best_t))
                if cap_t is not None:
                    best_t = min(float(best_t), float(cap_t))
        except Exception:
            pass

        best_pt: Point = (ox + best_t * ux, oy + best_t * uy)

        for seg in segments:
            hit, t, pt = _segment_intersect_ray((ox, oy), (ux, uy), seg)
            if not hit:
                continue
            if t > best_t:
                continue
            best_t = t
            best_pt = pt

        hits.append((ang, best_pt))

    hits.sort(key=lambda x: x[0])
    poly = [pt for _, pt in hits]
    poly = _compact_points(poly)

    return poly


def _compact_points(points: Sequence[Point], tol: float = 1e-6) -> List[Point]:
    if not points:
        return []
    out: List[Point] = []
    for p in points:
        if not out:
            out.append(p)
            continue
        if (abs(p[0] - out[-1][0]) <= tol) and (abs(p[1] - out[-1][1]) <= tol):
            continue
        out.append(p)
    # also compact closing edge
    if len(out) >= 2 and (abs(out[0][0] - out[-1][0]) <= tol) and (abs(out[0][1] - out[-1][1]) <= tol):
        out.pop()
    return out


def compute_player_visibility_polygons(
    tokens: Dict[str, Any],
    meta: Dict[str, Any],
    cols: int,
    rows: int,
    *,
    door_state: Optional[Dict[str, bool]] = None,
    feet_per_square: int = 5,
    default_vision_ft: int = 60,
    include_blocked_occluders: bool = False,
) -> List[List[Point]]:
    """
    Returns a list of polygons (one per player token), in grid-space.
    IMPORTANT: We DO NOT clamp polygon points; instead we add map boundary segments
    so rays naturally terminate at the map edge. Clamping causes wedge artifacts.
    """
    fps = max(1, int(feet_per_square))

    # Build occluders from metadata
    segs = build_segments_from_meta(
        meta or {},
        include_blocked=include_blocked_occluders,
        door_state=door_state,
    )

    # Add map boundary as occluders (prevents clamping artifacts)
    C = float(cols)
    R = float(rows)
    boundary: List[Segment] = [
        ((0.0, 0.0), (C, 0.0)),
        ((C, 0.0), (C, R)),
        ((C, R), (0.0, R)),
        ((0.0, R), (0.0, 0.0)),
    ]
    segs = _dedupe_segments(list(segs) + boundary)

    polys: List[List[Point]] = []

    medium_atten = make_medium_attenuator(meta or {})

    for _tid, ts in (tokens or {}).items():
        # Only player-side tokens contribute vision
        try:
            side = getattr(ts, "side", "")
        except Exception:
            side = ""
        if str(side) != "player":
            continue

        try:
            gx = int(getattr(ts, "grid_x", 0))
            gy = int(getattr(ts, "grid_y", 0))
        except Exception:
            continue

        try:
            vision_ft = int(getattr(ts, "vision_ft", default_vision_ft) or default_vision_ft)
        except Exception:
            vision_ft = int(default_vision_ft)

        radius_cells = max(0.0, float(vision_ft // fps))
        origin = (gx + 0.5, gy + 0.5)

        poly = compute_visibility_polygon(origin, segs, radius_cells, attenuator=medium_atten)
        polys.append(poly)

    return polys


def compute_player_visibility_polygons_by_token(
    tokens: Dict[str, Any],
    meta: Dict[str, Any],
    cols: int,
    rows: int,
    *,
    door_state: Optional[Dict[str, bool]] = None,
    feet_per_square: int = 5,
    default_vision_ft: int = 60,
    include_blocked_occluders: bool = False,
) -> Dict[str, List[Point]]:
    """
    Returns {token_id: polygon} for player tokens, in grid-space.

    This is used by PlayerView to derive cell-level reveal masks that can
    incorporate lighting/vision rules (BX4.2).
    """
    fps = max(1, int(feet_per_square))

    segs = build_segments_from_meta(
        meta or {},
        include_blocked=include_blocked_occluders,
        door_state=door_state,
    )

    # Add map boundary as occluders (prevents clamping artifacts)
    C = float(cols)
    R = float(rows)
    boundary: List[Segment] = [
        ((0.0, 0.0), (C, 0.0)),
        ((C, 0.0), (C, R)),
        ((C, R), (0.0, R)),
        ((0.0, R), (0.0, 0.0)),
    ]
    segs = _dedupe_segments(list(segs) + boundary)

    out: Dict[str, List[Point]] = {}

    base_meta = meta or {}
    medium_atten = make_medium_attenuator(base_meta)

    for tid, ts in (tokens or {}).items():
        try:
            side = getattr(ts, "side", "")
        except Exception:
            side = ""
        if str(side) != "player":
            continue

        try:
            gx = int(getattr(ts, "grid_x", 0))
            gy = int(getattr(ts, "grid_y", 0))
        except Exception:
            continue

        try:
            vision_ft = int(getattr(ts, "vision_ft", default_vision_ft) or default_vision_ft)
        except Exception:
            vision_ft = int(default_vision_ft)

        radius_cells = max(0.0, float(vision_ft // fps))
        origin = (gx + 0.5, gy + 0.5)

        lighting_atten = make_lighting_attenuator(base_meta, ts, feet_per_square=fps)

        def _combo(origin_xy: Point, dir_xy: Point, max_t: float) -> float:
            mt = float(max_t)
            try:
                mt = min(mt, float(medium_atten(origin_xy, dir_xy, mt)))
            except Exception:
                pass
            try:
                mt = min(mt, float(lighting_atten(origin_xy, dir_xy, mt)))
            except Exception:
                pass
            return mt

        poly = compute_visibility_polygon(origin, segs, radius_cells, attenuator=_combo)
        out[str(tid)] = poly

    return out
