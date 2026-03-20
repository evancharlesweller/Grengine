# ui/player_view_window.py
import os
import math
from typing import Dict, List, Optional, Set, Tuple

from PyQt5.QtWidgets import QMainWindow, QGraphicsScene, QGraphicsRectItem
from PyQt5.QtGui import QPixmap, QImage, QPainter, QColor, QPen, QBrush
from PyQt5.QtCore import QRectF, Qt
from typing import Optional
from ui.map_view import MapView
from ui.token import DraggableToken
from ui.encounter_state import EncounterState
from ui.constants import GRID_SIZE
from PyQt5.QtCore import QTimer

# Optional helper (if present). Lets player view compute weapon type/range exactly
# the same way as DM view / combat resolver.
try:
    from ui.item_lookup import get_weapon_or_unarmed
except Exception:
    get_weapon_or_unarmed = None
# Phase B2: deterministic visibility lives in engine
try:
    from engine.visibility_engine import compute_player_visible_cells
except Exception:
    compute_player_visible_cells = None
from PyQt5.QtGui import QPainter, QPainterPath, QColor
from PyQt5.QtCore import QPointF, Qt
try:
    from engine.visibility_polygon_engine import (
        compute_player_visibility_polygons,
        compute_player_visibility_polygons_by_token,
    )
except Exception:
    compute_player_visibility_polygons = None
    compute_player_visibility_polygons_by_token = None

try:
    from engine.perception_engine import can_perceive_cell
except Exception:
    can_perceive_cell = None

class PlayerViewWindow(QMainWindow):
    """
    Read-only "player stream" window:
      - Renders the map + fog of war
      - Shows PCs always
      - Shows NPCs only when currently visible (in vision)
      - Shows ONLY PC movement + PC attack range overlays (never NPC ranges)
      - Optionally shows AoE template preview if provided AND caster is a PC
    """

    def __init__(self, state: EncounterState):
        super().__init__()
        self.state = state
        self.setWindowTitle("Grengine - Player View")
        self.setGeometry(50, 50, 1000, 800)

        self.scene = QGraphicsScene()
        self.view = MapView(self.scene)

        # Lock interaction in player view
        self.view.setInteractive(False)
        self.view.setDragMode(self.view.NoDrag)
        self.view.setContextMenuPolicy(Qt.NoContextMenu)
        self.setCentralWidget(self.view)

        self.map_pixmap: Optional[QPixmap] = None
        self._token_items: Dict[str, DraggableToken] = {}
        # Performance: cache pixmaps/items to avoid full scene rebuild every refresh
        self._pv_map_item = None  # QGraphicsPixmapItem
        self._pv_map_relpath = None
        self._pv_pix_cache: Dict[str, QPixmap] = {}
        self._pv_initialized = False

        self._fog_item = None

        self._tick = QTimer(self)
        self._tick.timeout.connect(self.refresh)

        # Perf: Player View does not need 5 fps. 2 fps is plenty for streaming.
        # Heavy recompute is also cached (see refresh()).
        self._tick.start(500)  # 2 fps

        # Perf caches / signatures
        self._pv_last_sig = None           # full state signature
        self._pv_last_vis_sig = None       # signature used for visibility path
        self._pv_last_fog_sig = None       # signature used for fog pixmap
        self._pv_last_cols_rows = None     # (cols, rows)
        self._pv_cached_fog_pix = None     # QPixmap

        # These are set externally by MainWindow (optional)
        self.selected_token_id: Optional[str] = None      # current selection in DM view
        self.template_payload: Optional[dict] = None      # AoE preview payload (optional)

        self._overlay_items: List[QGraphicsRectItem] = []

    # -----------------------------
    # Fog of war (continuous polygon, NO explored memory)
    # -----------------------------

        
    def _point_in_poly(self, px: float, py: float, poly: List[Tuple[float, float]]) -> bool:
        """Ray-casting point-in-polygon (poly in grid-space)."""
        try:
            n = len(poly)
            if n < 3:
                return False
            inside = False
            j = n - 1
            for i in range(n):
                xi, yi = float(poly[i][0]), float(poly[i][1])
                xj, yj = float(poly[j][0]), float(poly[j][1])
                intersect = ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / ((yj - yi) if (yj - yi) != 0 else 1e-9) + xi)
                if intersect:
                    inside = not inside
                j = i
            return inside
        except Exception:
            return False

    def _rebuild_visibility_path(self, cols: int, rows: int) -> None:
        """Recompute live visibility union path (no explored memory).

        We keep the fog edge continuous (polygon union), so players can "peek"
        around corners. Lighting + vision types are enforced in the *polygon*
        generation (engine) via an attenuator, not by converting to a hard
        cell mask.
        """
        self._visibility_path = QPainterPath()
        self._visibility_path.setFillRule(Qt.WindingFill)

        if compute_player_visibility_polygons_by_token is None and compute_player_visibility_polygons is None:
            return

        # BX5.2: merge runtime fog zones (spawned during encounter) into meta for visibility attenuator
        meta = dict(getattr(self, "map_meta", {}) or {})
        try:
            base_z = list((meta.get("fog_zones", []) or []))
        except Exception:
            base_z = []
        try:
            rt = list(getattr(self.state, "runtime_fog_zones", []) or [])
        except Exception:
            rt = []
        if rt:
            meta["fog_zones"] = base_z + rt
        else:
            meta["fog_zones"] = base_z

        polys_by = None
        if compute_player_visibility_polygons_by_token is not None:
            try:
                polys_by = compute_player_visibility_polygons_by_token(
                    self.state.tokens,
                    meta,
                    cols,
                    rows,
                    door_state=getattr(self, "door_state", None) or getattr(self.state, "door_state", {}) or {},
                    feet_per_square=5,
                    default_vision_ft=60,
                    include_blocked_occluders=False,
                )
            except Exception:
                polys_by = None

        if polys_by is None:
            polys = compute_player_visibility_polygons(
                self.state.tokens,
                meta,
                cols,
                rows,
                door_state=getattr(self, "door_state", None) or getattr(self.state, "door_state", {}) or {},
                feet_per_square=5,
                default_vision_ft=60,
                include_blocked_occluders=False,
            ) or []
            polys_by = {"_union": p for p in polys if p and len(p) >= 3}

        for _tid, poly in (polys_by or {}).items():
            if not poly or len(poly) < 3:
                continue
            try:
                p0 = poly[0]
                path = QPainterPath()
                path.moveTo(QPointF(float(p0[0]) * GRID_SIZE, float(p0[1]) * GRID_SIZE))
                for (x, y) in poly[1:]:
                    path.lineTo(QPointF(float(x) * GRID_SIZE, float(y) * GRID_SIZE))
                path.closeSubpath()
                self._visibility_path.addPath(path)
            except Exception:
                continue

    def _build_fog_overlay_pixmap(self, width_px: int, height_px: int) -> QPixmap:
        """
        Fog = everything NOT currently visible.
        No explored memory. Full black outside visibility.
        """
        img = QImage(width_px, height_px, QImage.Format_ARGB32)
        img.fill(Qt.transparent)

        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Full fog
        painter.fillRect(0, 0, width_px, height_px, QColor(0, 0, 0, 255))

        # Punch out visibility union
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.fillPath(self._visibility_path, Qt.transparent)

        painter.end()
        return QPixmap.fromImage(img)


    def _is_token_visible_in_player_view(self, token_state) -> bool:
        """Render-only visibility check. Uses live visibility path."""
        try:
            cx = (int(getattr(token_state, "grid_x", 0)) + 0.5) * GRID_SIZE
            cy = (int(getattr(token_state, "grid_y", 0)) + 0.5) * GRID_SIZE
        except Exception:
            return False
        return self._visibility_path.contains(QPointF(cx, cy))

    # -----------------------------
    # Overlay helpers
    # -----------------------------
    def _clear_overlays(self) -> None:
        for it in self._overlay_items:
            try:
                self.scene.removeItem(it)
            except Exception:
                pass
        self._overlay_items = []

    def _circle_cells(self, cx: int, cy: int, radius: int, cols: int, rows: int) -> List[Tuple[int, int]]:
        """Euclidean circle of grid cells (NOT Manhattan diamond)."""
        out: List[Tuple[int, int]] = []
        r2 = radius * radius
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if dx * dx + dy * dy <= r2:
                    x = cx + dx
                    y = cy + dy
                    if 0 <= x < cols and 0 <= y < rows:
                        out.append((x, y))
        return out

    def _bresenham_line(self, x0: int, y0: int, x1: int, y1: int) -> List[Tuple[int, int]]:
        cells: List[Tuple[int, int]] = []
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        x, y = x0, y0
        while True:
            cells.append((x, y))
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy
        return cells

    def _cone_cells(self, ox: int, oy: int, tx: int, ty: int, length: int, angle_deg: int, cols: int, rows: int) -> List[Tuple[int, int]]:
        # Cone as angle test: dot(n, u) >= cos(theta/2)
        out: List[Tuple[int, int]] = []
        vx = tx - ox
        vy = ty - oy
        vlen = math.hypot(vx, vy)
        if vlen == 0:
            return out
        ux = vx / vlen
        uy = vy / vlen

        half = math.radians(angle_deg / 2.0)
        cos_thresh = math.cos(half)

        for dx in range(-length, length + 1):
            for dy in range(-length, length + 1):
                x = ox + dx
                y = oy + dy
                if not (0 <= x < cols and 0 <= y < rows):
                    continue

                dist = math.hypot(dx, dy)
                if dist == 0 or dist > length:
                    continue

                nx = dx / dist
                ny = dy / dist
                if (nx * ux + ny * uy) >= cos_thresh:
                    out.append((x, y))

        return out
    
    def _boundary_cells(self, cells: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        """
        Given a filled region (cells), return only the perimeter cells.
        A cell is on the perimeter if any 4-neighbor is missing.
        """
        s = set(cells)
        out = []
        for (x, y) in s:
            if ((x + 1, y) not in s) or ((x - 1, y) not in s) or ((x, y + 1) not in s) or ((x, y - 1) not in s):
                out.append((x, y))
        return out

    def _draw_cells_outline(self, cells, pen: QPen, z: float, brush: Optional[QBrush] = None):
        """
        Draw outlined (and optionally filled) grid squares for overlays.
        Must NOT accept mouse input. Must be above fog (fog is z=50).
        """
        scene_rect = self.scene.sceneRect()

        for (cx, cy) in cells:
            x = cx * GRID_SIZE
            y = cy * GRID_SIZE
            rectf = QRectF(x, y, GRID_SIZE, GRID_SIZE)

            if not scene_rect.intersects(rectf):
                continue

            r = QGraphicsRectItem(rectf)
            r.setPen(pen)

            # If brush is provided => filled. Otherwise => outline-only.
            r.setBrush(brush if brush is not None else QBrush(Qt.NoBrush))

            # Do not let overlays steal clicks / context menu
            r.setAcceptedMouseButtons(Qt.NoButton)
            r.setAcceptHoverEvents(False)
            r.setFlag(QGraphicsRectItem.ItemIsSelectable, False)
            r.setFlag(QGraphicsRectItem.ItemIsFocusable, False)

            r.setZValue(z)

            self.scene.addItem(r)
            self._overlay_items.append(r)

    # -----------------------------
    # PC overlay logic
    # -----------------------------
    def _pick_driver_pc(self) -> Optional[object]:
        """
        Choose which PC drives the overlays.

        Rule (per Evan request):
        - Overlays only show when the DM has a PC token SELECTED.
        - No initiative fallback. If not selected, show nothing.
        """
        sid = (self.selected_token_id or "").strip()
        if not sid:
            return None

        ts = self.state.tokens.get(sid)
        if ts and (getattr(ts, "side", "") == "player" or getattr(ts, "kind", "") == "pc"):
            return ts

        return None

    def _pc_weapon_range(self, ts) -> Tuple[str, int]:
        """
        Returns (weapon_type, range_squares).
        weapon_type: 'melee' | 'ranged'
        range_squares: >=1
        """
        weapon_ref = (getattr(ts, "weapon_id", "") or getattr(ts, "weapon", "") or "unarmed").strip()
        weapon_data = {}

        if get_weapon_or_unarmed is not None:
            try:
                weapon_data = get_weapon_or_unarmed(self.state.campaign_path, weapon_ref) or {}
            except Exception:
                weapon_data = {}

        wtype = str(weapon_data.get("type", "melee") or "melee").lower().strip()
        rng_ft = weapon_data.get("range_ft", weapon_data.get("range", 5))
        try:
            rng_ft = int(rng_ft)
        except Exception:
            rng_ft = 5

        rng_sq = max(1, rng_ft // 5)
        return wtype, rng_sq

    def _draw_pc_overlays(self, cols: int, rows: int) -> None:
        ts = self._pick_driver_pc()
        if not ts:
            return

        cx, cy = int(ts.grid_x), int(ts.grid_y)

        # --- Movement: use remaining when initiative is active AND this PC is the active token ---
        # --- Movement: use remaining when initiative is active AND this PC is the active token ---
        move_ft = int(getattr(ts, "movement", 0) or 0)

        if bool(getattr(self.state, "initiative_active", False)):
            active_id = getattr(self.state, "active_token_id", None)
            if active_id and getattr(ts, "token_id", None) == active_id:
                mr = getattr(ts, "movement_remaining", None)   # IMPORTANT: allow 0
                if mr is not None:
                    move_ft = int(mr)

        move_sq = max(0, move_ft // 5)

        move_sq = max(0, move_ft // 5)

        wtype, rng_sq = self._pc_weapon_range(ts)

        # Movement (blue)
        # Movement (blue)
        # Movement (blue) — FILLED
        # Movement (blue) — FILLED
        if move_sq > 0:
            pen_move = QPen(QColor(80, 170, 255, 200))
            pen_move.setWidth(2)

            move_brush = QBrush(QColor(0, 120, 255, 50))  # translucent fill like DM view

            self._draw_cells_outline(
                self._circle_cells(cx, cy, move_sq, cols, rows),
                pen_move,
                z=60,
                brush=move_brush,
            )

        # Range (red)
        pen_rng = QPen(QColor(255, 80, 80, 230))
        pen_rng.setWidth(2)

        if wtype == "melee":
            melee = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    x, y = cx + dx, cy + dy
                    if 0 <= x < cols and 0 <= y < rows:
                        melee.append((x, y))
            self._draw_cells_outline(melee, pen_rng, z=61) 
        else:
            self._draw_cells_outline(self._circle_cells(cx, cy, rng_sq, cols, rows), pen_rng, z=61)  # no fill

    # -----------------------------
    # AoE template preview (optional)
    # -----------------------------
    def _draw_template(self, cols: int, rows: int) -> None:
        payload = self.template_payload
        if not isinstance(payload, dict):
            return

        caster_id = str(payload.get("caster_token_id", "") or "").strip()
        if not caster_id or caster_id not in self.state.tokens:
            return

        caster = self.state.tokens[caster_id]

        # Safety: only PC-cast templates can appear on player view
        if not (getattr(caster, "side", "") == "player" or getattr(caster, "kind", "") == "pc"):
            return

        target = payload.get("target_cell", None)
        if not target or not isinstance(target, (list, tuple)) or len(target) != 2:
            return

        tx, ty = int(target[0]), int(target[1])
        ox, oy = int(caster.grid_x), int(caster.grid_y)

        templ = payload.get("template", {}) or {}
        shape = str(templ.get("shape", "")).lower().strip()

        pen_tpl = QPen(QColor(180, 120, 255, 235))  # purple-ish
        pen_tpl.setWidth(2)

        if shape == "radius":
            radius_ft = templ.get("radius_ft", 15)
            try:
                radius_ft = int(radius_ft)
            except Exception:
                radius_ft = 15
            r_sq = max(1, radius_ft // 5)
            self._draw_cells_outline(self._circle_cells(tx, ty, r_sq, cols, rows), pen_tpl, z=70)

        elif shape == "line":
            length_ft = templ.get("line_length_ft", 30)
            try:
                length_ft = int(length_ft)
            except Exception:
                length_ft = 30
            length_sq = max(1, length_ft // 5)

            dx = tx - ox
            dy = ty - oy
            dist = math.hypot(dx, dy)
            if dist == 0:
                return

            ux, uy = dx / dist, dy / dist
            ex = int(round(ox + ux * length_sq))
            ey = int(round(oy + uy * length_sq))

            cells = self._bresenham_line(ox, oy, ex, ey)[: length_sq + 1]
            self._draw_cells_outline(cells, pen_tpl, z=70)

        elif shape == "cone":
            length_ft = templ.get("cone_length_ft", 30)
            angle_deg = templ.get("cone_angle_deg", 90)
            try:
                length_ft = int(length_ft)
            except Exception:
                length_ft = 30
            try:
                angle_deg = int(angle_deg)
            except Exception:
                angle_deg = 90

            length_sq = max(1, length_ft // 5)
            cells = self._cone_cells(ox, oy, tx, ty, length_sq, angle_deg, cols, rows)
            self._draw_cells_outline(cells, pen_tpl, z=70)

    # -----------------------------
    # Main refresh
    # -----------------------------
    def refresh(self) -> None:
        """Refresh Player View.

        Performance note: We do NOT clear/rebuild the entire QGraphicsScene on every
        update. We keep persistent map/token items and only update:
          - visibility path + fog pixmap
          - token positions + HP bars
          - add/remove tokens as needed
        """

        # Render map (init or map change)
        if not getattr(self, "state", None):
            return

        if not getattr(self.state, "map_relpath", None):
            return

        map_rel = str(self.state.map_relpath)
        map_path = os.path.join(self.state.campaign_path, map_rel)
        if not os.path.exists(map_path):
            return

        # Load or reuse cached map pixmap
        if self._pv_map_relpath != map_rel or getattr(self, "map_pixmap", None) is None:
            pix = QPixmap(map_path)
            self.map_pixmap = pix
            self._pv_map_relpath = map_rel
            if self._pv_map_item is None:
                self._pv_map_item = self.scene.addPixmap(pix)
            else:
                self._pv_map_item.setPixmap(pix)
            # Scene rect should match map
            self.scene.setSceneRect(QRectF(pix.rect()))

        if not getattr(self, "map_pixmap", None):
            return

        width_px = self.map_pixmap.width()
        height_px = self.map_pixmap.height()
        cols = math.ceil(width_px / GRID_SIZE)
        rows = math.ceil(height_px / GRID_SIZE)

        # PERF: skip heavy fog recompute if nothing that affects it changed
        sig = self._pv_build_signature(cols, rows)
        if self._pv_last_sig == sig:
            # Still update token positions (cheap) and overlays (moderate)
            # but avoid rebuilding visibility + fog pixmap.
            pass
        else:
            self._pv_last_sig = sig

        # Vision / fog (continuous) — rebuild ONLY when signature changes
        if self._pv_last_fog_sig != sig:
            self._pv_last_fog_sig = sig

            self._rebuild_visibility_path(cols, rows)
            self._pv_cached_fog_pix = self._build_fog_overlay_pixmap(width_px, height_px)

            if self._fog_item is None:
                fog_item = self.scene.addPixmap(self._pv_cached_fog_pix)
                fog_item.setPos(0, 0)
                fog_item.setZValue(50)
                self._fog_item = fog_item
            else:
                self._fog_item.setPixmap(self._pv_cached_fog_pix)

        # Tokens: update or create
        live_ids = set()
        for token_id, ts in (getattr(self.state, "tokens", {}) or {}).items():
            try:
                token_id = str(token_id)
            except Exception:
                continue
            live_ids.add(token_id)

            # Load pixmap from cache
            rel = str(getattr(ts, "image_relpath", "") or "")
            if not rel:
                continue
            token_path = os.path.join(self.state.campaign_path, rel)
            if not os.path.exists(token_path):
                continue

            pix = self._pv_pix_cache.get(rel)
            if pix is None:
                pix = QPixmap(token_path)
                self._pv_pix_cache[rel] = pix

            item = self._token_items.get(token_id)
            if item is None:
                item = DraggableToken(pix, grid_size=GRID_SIZE)
                item.token_id = getattr(ts, "token_id", token_id)
                item.setFlag(item.ItemIsMovable, False)
                item.setFlag(item.ItemIsSelectable, False)
                item.setAcceptedMouseButtons(Qt.NoButton)
                item.setZValue(5)
                self.scene.addItem(item)
                self._token_items[token_id] = item
            else:
                # Update pixmap if relpath changed
                try:
                    # DraggableToken uses pixmap(); compare cache key instead of object
                    if getattr(item, "_pv_rel", None) != rel:
                        item.setPixmap(pix)
                except Exception:
                    pass

            # Remember what image this item corresponds to
            try:
                item._pv_rel = rel
            except Exception:
                pass

            # Update token fields for HP bar rendering
            try:
                item.display_name = getattr(ts, "display_name", token_id)
                item.hp = getattr(ts, "hp", 0)
                item.max_hp = getattr(ts, "max_hp", 0)
                item.ac = getattr(ts, "ac", 0)
                item.weapon = getattr(ts, "weapon_id", "") or getattr(ts, "weapon", "")
                item.armor = getattr(ts, "armor_id", "") or getattr(ts, "armor", "")
                item.movement = getattr(ts, "movement", 0)
                item.attack_modifier = getattr(ts, "attack_modifier", 0)
                item.side = getattr(ts, "side", "")
                item.update_hp_bar()
            except Exception:
                pass

            # Position
            try:
                item.setPos(int(getattr(ts, "grid_x", 0)) * GRID_SIZE, int(getattr(ts, "grid_y", 0)) * GRID_SIZE)
            except Exception:
                pass

        # Remove stale token items
        for tid in list(self._token_items.keys()):
            if tid not in live_ids:
                try:
                    self.scene.removeItem(self._token_items[tid])
                except Exception:
                    pass
                try:
                    del self._token_items[tid]
                except Exception:
                    pass

        # PC overlays + AoE template overlays ABOVE fog
        self._clear_overlays()
        self._draw_pc_overlays(cols, rows)
        self._draw_template(cols, rows)

        # Nudge repaint
        try:
            self.scene.update()
        except Exception:
            pass

    def _pv_build_signature(self, cols: int, rows: int) -> tuple:
        """
        Build a cheap, deterministic signature of everything that affects:
          - visibility polygons/path
          - fog pixmap
          - token visibility

        If signature hasn't changed, we can skip recomputing fog entirely.
        """
        st = getattr(self, "state", None)
        if st is None:
            return ("no_state", cols, rows)

        # Map identity
        map_rel = str(getattr(st, "map_relpath", "") or "")

        # Door state overrides (sorted)
        ds = getattr(self, "door_state", None) or getattr(st, "door_state", {}) or {}
        try:
            door_part = tuple(sorted((str(k), bool(v)) for k, v in (ds or {}).items()))
        except Exception:
            door_part = tuple()

        # Runtime fog zones + authored fog zones (sorted-ish)
        meta = getattr(self, "map_meta", {}) or {}
        base_z = meta.get("fog_zones", []) or []
        rt_z = getattr(st, "runtime_fog_zones", []) or []

        def _zone_key(z):
            if not isinstance(z, dict):
                return None
            try:
                return (
                    str(z.get("kind", "")),
                    float(z.get("cx", 0.0)),
                    float(z.get("cy", 0.0)),
                    float(z.get("r", 0.0)),
                    float(z.get("density", 0.0)),
                    int(z.get("ttl_turns", -1)) if z.get("ttl_turns", None) is not None else -1,
                )
            except Exception:
                return None

        zones_part = tuple(sorted([k for k in (_zone_key(z) for z in (list(base_z) + list(rt_z))) if k is not None]))

        # Lighting / foliage / mediums affect attenuator; include their dict items
        lighting = meta.get("lighting", {}) or {}
        foliage = meta.get("foliage", {}) or {}
        mediums = meta.get("mediums", {}) or {}  # if present in your build

        def _dict_part(d):
            if not isinstance(d, dict):
                return tuple()
            try:
                return tuple(sorted((str(k), str(v)) for k, v in d.items()))
            except Exception:
                return tuple()

        lighting_part = _dict_part(lighting)
        foliage_part = _dict_part(foliage)
        mediums_part = _dict_part(mediums)

        # Player tokens that contribute vision: id, pos, vision fields + vision type
        toks = getattr(st, "tokens", {}) or {}
        pv = []
        for tid, ts in toks.items():
            try:
                side = str(getattr(ts, "side", "") or "")
                kind = str(getattr(ts, "kind", "") or "")
            except Exception:
                side = ""
                kind = ""
            if not (side == "player" or kind == "pc"):
                continue
            try:
                pv.append((
                    str(getattr(ts, "token_id", tid) or tid),
                    int(getattr(ts, "grid_x", 0)),
                    int(getattr(ts, "grid_y", 0)),
                    int(getattr(ts, "vision_ft", 60) or 60),
                    str(getattr(ts, "vision_type", "") or getattr(ts, "sense", "") or "normal"),
                ))
            except Exception:
                continue
        pv_part = tuple(sorted(pv))

        return (map_rel, cols, rows, door_part, zones_part, lighting_part, foliage_part, mediums_part, pv_part)