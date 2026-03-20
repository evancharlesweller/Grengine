# ui/map_metadata_editor.py
from __future__ import annotations

from typing import Callable, Optional, Dict, Any, List, Tuple

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox, QLineEdit, QSpinBox, QMessageBox
)
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QGraphicsLineItem, QGraphicsRectItem, QGraphicsEllipseItem
from PyQt5.QtWidgets import QCheckBox
from PyQt5.QtGui import QPen, QColor, QBrush
from PyQt5.QtWidgets import (
    QGraphicsRectItem,
    QGraphicsLineItem,
    QGraphicsEllipseItem,
)
from ui.constants import GRID_SIZE
from engine.map_metadata import pick_cell, pick_edge
from engine.map_metadata import toggle_wall, toggle_blocked_cell, set_terrain, set_hazard, clear_hazard
from engine.services.map_metadata_service import ensure_loaded, save as save_meta

class MapMetadataEditorWidget(QWidget):
    """DM tool: paint edge-walls, blocked cells, and terrain IDs; save to <map>.meta.json.

    UI responsibility: render overlays + dispatch clicks. Engine handles picking + mutations.
    """

    MODES = ("Walls", "Doors", "Half Walls", "Blocked", "Terrain", "Elevation", "Drop Edges", "Lighting", "Hazards", "Foliage", "Fog Zones")

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.mode = "Walls"
        self._terrain_id = "difficult"
        self._overlay_items: List[Any] = []
        self._loaded_map_path = None

        # Create checkbox only (do NOT add to layout here)
        self.chk_enable_paint = QCheckBox("Enable painting")
        self.chk_enable_paint.setChecked(True)
        self.chk_enable_paint.toggled.connect(self._on_enable_paint_toggled)
        
        self._erase_mode = False

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(self.chk_enable_paint)

        self.btn_erase = QPushButton("Erase")
        self.btn_erase.setCheckable(True)
        self.btn_erase.setChecked(False)
        self.btn_erase.toggled.connect(self._on_erase_toggled)

        # Add to your controls layout:
        layout.addWidget(self.btn_erase)   # replace controls_layout with your actual layout variable

        title = QLabel("Map Metadata Editor")
        title.setAlignment(Qt.AlignLeft)
        layout.addWidget(title)

        row = QHBoxLayout()
        self.btn_walls = QPushButton("Walls")
        self.btn_doors = QPushButton("Doors")
        self.btn_half_walls = QPushButton("Half Walls")
        self.btn_blocked = QPushButton("Blocked")
        self.btn_terrain = QPushButton("Terrain")
        self.btn_elevation = QPushButton("Elevation")
        self.btn_drop_edges = QPushButton("Drop Edges")

        self.btn_lighting = QPushButton("Lighting")
        self.btn_hazards = QPushButton("Hazards")

        # BX5.1: environmental mediums
        self.btn_foliage = QPushButton("Foliage")
        self.btn_fog_zones = QPushButton("Fog Zones")

        for b in (
            self.btn_walls,
            self.btn_doors,
            self.btn_half_walls,
            self.btn_blocked,
            self.btn_terrain,
            self.btn_elevation,
            self.btn_drop_edges,
            self.btn_lighting,
            self.btn_hazards,
            self.btn_foliage,
            self.btn_fog_zones,
        ):
            b.setCheckable(True)
            row.addWidget(b)
        layout.addLayout(row)

        self.btn_walls.clicked.connect(lambda: self.set_mode("Walls"))
        self.btn_doors.clicked.connect(lambda: self.set_mode("Doors"))
        self.btn_half_walls.clicked.connect(lambda: self.set_mode("Half Walls"))
        self.btn_blocked.clicked.connect(lambda: self.set_mode("Blocked"))
        self.btn_terrain.clicked.connect(lambda: self.set_mode("Terrain"))
        self.btn_elevation.clicked.connect(lambda: self.set_mode("Elevation"))
        self.btn_drop_edges.clicked.connect(lambda: self.set_mode("Drop Edges"))

        self.btn_lighting.clicked.connect(lambda: self.set_mode("Lighting"))
        self.btn_hazards.clicked.connect(lambda: self.set_mode("Hazards"))

        self.btn_foliage.clicked.connect(lambda: self.set_mode("Foliage"))
        self.btn_fog_zones.clicked.connect(lambda: self.set_mode("Fog Zones"))

        trow = QHBoxLayout()
        trow.addWidget(QLabel("Terrain ID:"))
        self.terrain_combo = QComboBox()
        self.terrain_combo.setEditable(True)
        self.terrain_combo.addItems(["difficult",  "ice",  "uneven",  "mud",  "sand",  "water"])
        self.terrain_combo.setCurrentText(self._terrain_id)
        self.terrain_combo.currentTextChanged.connect(self._on_terrain_changed)



        # Elevation controls (BX3)
        erow = QHBoxLayout()
        erow.addWidget(QLabel("Elevation ft:"))
        self.elevation_spin = QSpinBox()
        self.elevation_spin.setRange(-1000, 1000)
        self.elevation_spin.setValue(0)
        erow.addWidget(self.elevation_spin)
        layout.addLayout(erow)

        # Drop edge controls (BX3.2)
        drow = QHBoxLayout()
        drow.addWidget(QLabel("Drop ft:"))

        self.drop_edge_spin = QSpinBox()
        self.drop_edge_spin.setRange(0, 1000)
        self.drop_edge_spin.setValue(0)  # 0 = no explicit drop_ft stored
        drow.addWidget(self.drop_edge_spin)

        layout.addLayout(drow)

        # Hazard controls (BX1)
        hrow = QHBoxLayout()
        hrow.addWidget(QLabel("Hazard:"))
        self.hazard_type_combo = QComboBox()
        self.hazard_type_combo.setEditable(True)
        self.hazard_type_combo.addItems(["fire", "acid", "poison", "pit", "spikes", "cold", "electric"])
        hrow.addWidget(self.hazard_type_combo)

        # Hazard trigger checkboxes (single canonical set)
        hrow.addWidget(QLabel("Triggers:"))
        self.haz_enter_cb = QCheckBox("Enter")
        self.haz_enter_cb.setChecked(True)
        self.haz_turn_start_cb = QCheckBox("Turn Start")
        self.haz_turn_end_cb = QCheckBox("Turn End")
        hrow.addWidget(self.haz_enter_cb)
        hrow.addWidget(self.haz_turn_start_cb)
        hrow.addWidget(self.haz_turn_end_cb)
        hrow.addWidget(QLabel("Damage:"))
        self.hazard_damage_edit = QLineEdit()
        self.hazard_damage_edit.setPlaceholderText("e.g. 1d6, 2d4+1, 3")
        self.hazard_damage_edit.setText("1d4")
        hrow.addWidget(self.hazard_damage_edit)

        layout.addLayout(hrow)

        hsrow = QHBoxLayout()
        hsrow.addWidget(QLabel("Save:"))
        self.hazard_save_ability_combo = QComboBox()
        self.hazard_save_ability_combo.addItems(["None", "STR", "DEX", "CON", "INT", "WIS", "CHA"])
        hsrow.addWidget(self.hazard_save_ability_combo)
        hsrow.addWidget(QLabel("DC:"))
        self.hazard_save_dc_spin = QSpinBox()
        self.hazard_save_dc_spin.setRange(1, 99)
        self.hazard_save_dc_spin.setValue(10)
        hsrow.addWidget(self.hazard_save_dc_spin)
        hsrow.addWidget(QLabel("On Success:"))
        self.hazard_save_success_combo = QComboBox()
        self.hazard_save_success_combo.addItems(["none", "half", "full"])
        self.hazard_save_success_combo.setCurrentText("none")
        hsrow.addWidget(self.hazard_save_success_combo)
        hsrow.addWidget(QLabel("Mode:"))
        self.hazard_save_mode_combo = QComboBox()
        self.hazard_save_mode_combo.addItems(["normal", "advantage", "disadvantage"])
        hsrow.addWidget(self.hazard_save_mode_combo)
        layout.addLayout(hsrow)

        # Lighting controls (BX4+ authoring)
        lrow = QHBoxLayout()
        lrow.addWidget(QLabel("Light:"))
        self.lighting_combo = QComboBox()
        self.lighting_combo.setEditable(False)
        self.lighting_combo.addItems(["bright", "dim", "dark", "magical_dark"])
        self.lighting_combo.setCurrentText("bright")
        lrow.addWidget(self.lighting_combo)
        layout.addLayout(lrow)
        trow.addWidget(self.terrain_combo)
        layout.addLayout(trow)

        # BX5.1: Foliage (tree line / soft medium) authoring
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Foliage Density:"))
        self.foliage_combo = QComboBox()
        self.foliage_combo.setEditable(False)
        self.foliage_combo.addItems(["0.25", "0.50", "0.75", "1.00"])
        self.foliage_combo.setCurrentText("0.50")
        frow.addWidget(self.foliage_combo)
        layout.addLayout(frow)

        # BX5.1: Fog Zones (gas/smoke field) authoring
        zrow = QHBoxLayout()
        zrow.addWidget(QLabel("Fog Zone:"))
        zrow.addWidget(QLabel("Radius (cells):"))
        self.fog_radius_edit = QLineEdit()
        self.fog_radius_edit.setFixedWidth(60)
        self.fog_radius_edit.setText("3")
        zrow.addWidget(self.fog_radius_edit)
        zrow.addWidget(QLabel("Density:"))
        self.fog_density_combo = QComboBox()
        self.fog_density_combo.setEditable(False)
        self.fog_density_combo.addItems(["0.25", "0.50", "0.75", "1.00"])
        self.fog_density_combo.setCurrentText("0.50")
        zrow.addWidget(self.fog_density_combo)
        layout.addLayout(zrow)

        brow = QHBoxLayout()
        self.btn_save = QPushButton("Save .meta.json")
        self.btn_reload = QPushButton("Reload")
        self.btn_clear_overlays = QPushButton("Clear Overlays")
        brow.addWidget(self.btn_save)
        brow.addWidget(self.btn_reload)
        brow.addWidget(self.btn_clear_overlays)
        layout.addLayout(brow)

        self.btn_save.clicked.connect(self.save_meta)
        self.btn_reload.clicked.connect(self.reload_meta)
        self.btn_clear_overlays.clicked.connect(self.redraw_overlays)

        layout.addStretch(1)

        self.set_mode("Walls")

    def _on_terrain_changed(self, txt: str):
        self._terrain_id = (txt or "").strip() or "difficult"

    # ---------- Integration hooks ----------
    def enable_on_map(self, enabled: bool):
        mv = getattr(self.main_window, "map_view", None)
        if mv is None:
            return

        # If checkbox exists and is off, always disable routing.
        if hasattr(self, "chk_enable_paint") and not self.is_painting_enabled():
            enabled = False

        if enabled:
            mv.set_meta_edit_enabled(True, self._handle_map_click)
        else:
            mv.set_meta_edit_enabled(False, None)

    def set_mode(self, mode: str):
        mode = mode if mode in self.MODES else "Walls"
        self.mode = mode
        self.btn_walls.setChecked(mode == "Walls")
        if hasattr(self, "btn_doors"):
            self.btn_doors.setChecked(mode == "Doors")
        if hasattr(self, "btn_half_walls"):
            self.btn_half_walls.setChecked(mode == "Half Walls")
        self.btn_blocked.setChecked(mode == "Blocked")
        self.btn_terrain.setChecked(mode == "Terrain")
        if hasattr(self, "btn_elevation"):
            self.btn_elevation.setChecked(mode == "Elevation")
        if hasattr(self, "btn_drop_edges"):
            self.btn_drop_edges.setChecked(mode == "Drop Edges")
        if hasattr(self, "btn_lighting"):
            self.btn_lighting.setChecked(mode == "Lighting")
        if hasattr(self, "btn_hazards"):
            self.btn_hazards.setChecked(mode == "Hazards")
        if hasattr(self, "btn_foliage"):
            self.btn_foliage.setChecked(mode == "Foliage")
        if hasattr(self, "btn_fog_zones"):
            self.btn_fog_zones.setChecked(mode == "Fog Zones")

    # ---------- Meta access ----------
    def _get_map_dims_cells(self) -> Tuple[int, int]:
        pixmap = getattr(self.main_window, "map_pixmap", None)
        if pixmap is None:
            return (0, 0)
        w_px = int(pixmap.width())
        h_px = int(pixmap.height())
        return (max(1, w_px // GRID_SIZE), max(1, h_px // GRID_SIZE))

    def _ensure_meta_loaded(self, force: bool = False) -> bool:
        path = getattr(self.main_window, "current_map_path", "") or ""
        if not path:
            QMessageBox.warning(self, "No map loaded", "Load a map image first.")
            return False

        # If already loaded for this map and not forcing, do NOT reload from disk.
        if (not force
                and getattr(self.main_window, "current_map_meta", None) is not None
                and self._loaded_map_path == path):
            return True

        w, h = self._get_map_dims_cells()
        meta, meta_path = ensure_loaded(path, w, h, GRID_SIZE)

        self.main_window.current_map_meta = meta
        self.main_window.current_map_meta_path = meta_path
        self._loaded_map_path = path
        return True

    def reload_meta(self):
        if not self._ensure_meta_loaded(force=True):
            return
        self.redraw_overlays()

    def save_meta(self):
        path = getattr(self.main_window, "current_map_path", "") or ""
        meta = getattr(self.main_window, "current_map_meta", None)
        if not path or not isinstance(meta, dict):
            QMessageBox.warning(self, "Nothing to save", "Load a map and edit metadata first.")
            return
        w, h = self._get_map_dims_cells()
        ok, meta_path = save_meta(path, meta, w, h, GRID_SIZE)
        if ok:
            self.main_window.current_map_meta_path = meta_path
            QMessageBox.information(self, "Saved", f"Saved metadata to:\n{meta_path}")
        else:
            QMessageBox.warning(self, "Save failed", f"Could not save metadata to:\n{meta_path}")

    # ---------- Overlay rendering ----------
    def _clear_overlay_items(self):
        scene = getattr(self.main_window, "scene", None)
        if scene is None:
            return
        for it in self._overlay_items:
            try:
                scene.removeItem(it)
            except Exception:
                pass
        self._overlay_items = []

    def redraw_overlays(self):
        self._clear_overlay_items()
        if not self._ensure_meta_loaded(force=False):
            return

        scene = getattr(self.main_window, "scene", None)
        meta = getattr(self.main_window, "current_map_meta", {}) or {}
        if scene is None:
            return

        # Blocked cells
        blocked = meta.get("blocked", []) or []
        pen_block = QPen(QColor(255, 0, 0, 180))
        for b in blocked:
            try:
                x, y = int(b[0]), int(b[1])
            except Exception:
                continue
            r = QGraphicsRectItem(x * GRID_SIZE, y * GRID_SIZE, GRID_SIZE, GRID_SIZE)
            r.setPen(pen_block)
            r.setBrush(QColor(255, 0, 0, 40))
            r.setBrush(QColor(255, 0, 0, 255))  # solid brush, opacity handles transparency
            r.setOpacity(0.10) 
            r.setZValue(50)
            scene.addItem(r)
            self._overlay_items.append(r)

        # Lighting cells (BX4+ authoring). Stored as meta['lighting']["x,y"] = level.
        lighting = meta.get("lighting", {}) or {}
        if isinstance(lighting, dict) and lighting:
            # Theme-fit but distinct:
            # bright: warm gold, dim: cool blue-gray, dark: deep purple, magical_dark: violet
            level_brush = {
                "bright": QColor(255, 220, 100, 255),
                "dim": QColor(120, 160, 200, 255),
                "dark": QColor(80, 60, 120, 255),
                "magical_dark": QColor(140, 60, 160, 255),
            }
            pen_light = QPen(QColor(0, 0, 0, 0))

            for k, lvl in list(lighting.items()):
                try:
                    sx, sy = str(k).split(",")
                    x = int(sx)
                    y = int(sy)
                except Exception:
                    continue
                lvl = str(lvl or "bright").strip().lower()
                brush = level_brush.get(lvl)
                if brush is None:
                    brush = level_brush["bright"]

                r = QGraphicsRectItem(x * GRID_SIZE, y * GRID_SIZE, GRID_SIZE, GRID_SIZE)
                r.setPen(pen_light)
                r.setBrush(brush)
                # Keep lighting subtle under other overlays
                r.setOpacity(0.10)
                r.setZValue(45)
                scene.addItem(r)
                self._overlay_items.append(r)

        # BX5.1: Foliage density (tree line medium). meta['foliage']["x,y"] = float[0..1]
        foliage = meta.get("foliage", {}) or {}
        if isinstance(foliage, dict) and foliage:
            pen_f = QPen(QColor(0, 0, 0, 0))
            for k, v in list(foliage.items()):
                try:
                    sx, sy = str(k).split(",")
                    x = int(sx)
                    y = int(sy)
                    dens = float(v)
                except Exception:
                    continue
                dens = max(0.0, min(1.0, dens))
                if dens <= 0.0:
                    continue

                r = QGraphicsRectItem(x * GRID_SIZE, y * GRID_SIZE, GRID_SIZE, GRID_SIZE)
                r.setPen(pen_f)
                r.setBrush(QColor(40, 140, 40, 255))
                # Slightly stronger than lighting; scales with density.
                r.setOpacity(0.05 + 0.20 * dens)
                r.setZValue(46)
                scene.addItem(r)
                self._overlay_items.append(r)

        # BX5.1: Fog zones (gas/smoke circles). meta['fog_zones'] = [{cx,cy,r,density}, ...]
        zones = meta.get("fog_zones", []) or []
        if isinstance(zones, list) and zones:
            pen_z = QPen(QColor(30, 30, 30, 140))
            pen_z.setWidth(2)
            for z in list(zones):
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
                # Draw in pixel space
                px = (cx - rr) * GRID_SIZE
                py = (cy - rr) * GRID_SIZE
                w = (2.0 * rr) * GRID_SIZE
                h = (2.0 * rr) * GRID_SIZE
                e = QGraphicsEllipseItem(px, py, w, h)
                e.setPen(pen_z)
                e.setBrush(QColor(120, 140, 160, 255))
                e.setOpacity(0.05 + 0.20 * dens)
                e.setZValue(44)
                scene.addItem(e)
                self._overlay_items.append(e)

        # Walls (edge-based)
        walls = meta.get("walls", []) or []
        pen_wall = QPen(QColor(0, 0, 0, 220))
        pen_wall.setWidth(4)
        for w in walls:
            if not isinstance(w, dict):
                continue
            try:
                x = int(w.get("x"))
                y = int(w.get("y"))
                d = str(w.get("dir", "N")).upper()
            except Exception:
                continue
            x0 = x * GRID_SIZE
            y0 = y * GRID_SIZE
            if d == "N":
                x1, y1, x2, y2 = x0, y0, x0 + GRID_SIZE, y0
            elif d == "S":
                x1, y1, x2, y2 = x0, y0 + GRID_SIZE, x0 + GRID_SIZE, y0 + GRID_SIZE
            elif d == "W":
                x1, y1, x2, y2 = x0, y0, x0, y0 + GRID_SIZE
            else:  # E
                x1, y1, x2, y2 = x0 + GRID_SIZE, y0, x0 + GRID_SIZE, y0 + GRID_SIZE

            li = QGraphicsLineItem(x1, y1, x2, y2)
            li.setPen(pen_wall)
            li.setZValue(60)
            scene.addItem(li)
            self._overlay_items.append(li)

        
        # Doors (edge-based; closed=open state is not shown here - this is authoring overlay)
        doors = meta.get("doors", []) or []
        pen_door = QPen(QColor(120, 70, 20, 220))
        pen_door.setWidth(4)
        for dd in doors:
            if not isinstance(dd, dict):
                continue
            try:
                x = int(dd.get("x"))
                y = int(dd.get("y"))
                d = str(dd.get("edge", "N")).upper()
            except Exception:
                continue
            x0 = x * GRID_SIZE
            y0 = y * GRID_SIZE
            if d == "N":
                x1, y1, x2, y2 = x0, y0, x0 + GRID_SIZE, y0
            elif d == "S":
                x1, y1, x2, y2 = x0, y0 + GRID_SIZE, x0 + GRID_SIZE, y0 + GRID_SIZE
            elif d == "W":
                x1, y1, x2, y2 = x0, y0, x0, y0 + GRID_SIZE
            else:  # E
                x1, y1, x2, y2 = x0 + GRID_SIZE, y0, x0 + GRID_SIZE, y0 + GRID_SIZE
            li = QGraphicsLineItem(x1, y1, x2, y2)
            li.setPen(pen_door)
            li.setZValue(59)  # just under walls
            scene.addItem(li)
            self._overlay_items.append(li)



        # Half-walls / windows (edge-based; cover-only authoring overlay)
        halfs = meta.get("half_walls", []) or []
        pen_half = QPen(QColor(30, 120, 200, 220))
        pen_half.setWidth(3)
        # Use a dash style so half-walls are visually distinct
        try:
            pen_half.setStyle(Qt.DashLine)
        except Exception:
            pass

        for hw in halfs:
            if not isinstance(hw, dict):
                continue
            try:
                x = int(hw.get("x"))
                y = int(hw.get("y"))
                d = str(hw.get("dir", hw.get("edge", "N"))).upper()
            except Exception:
                continue
            x0 = x * GRID_SIZE
            y0 = y * GRID_SIZE
            if d == "N":
                x1, y1, x2, y2 = x0, y0, x0 + GRID_SIZE, y0
            elif d == "S":
                x1, y1, x2, y2 = x0, y0 + GRID_SIZE, x0 + GRID_SIZE, y0 + GRID_SIZE
            elif d == "W":
                x1, y1, x2, y2 = x0, y0, x0, y0 + GRID_SIZE
            else:  # E
                x1, y1, x2, y2 = x0 + GRID_SIZE, y0, x0 + GRID_SIZE, y0 + GRID_SIZE

            li = QGraphicsLineItem(x1, y1, x2, y2)
            li.setPen(pen_half)
            li.setZValue(58)
            scene.addItem(li)
            self._overlay_items.append(li)

        
        # Drop edges (BX3.2): cliffs/pits boundaries (edge-based)
        drop_edges = meta.get("drop_edges", []) or []
        if isinstance(drop_edges, list) and drop_edges:
            pen_de = QPen(QColor(200, 60, 200, 230))
            pen_de.setWidth(3)
            try:
                pen_de.setStyle(Qt.DotLine)
            except Exception:
                pass

            for de in drop_edges:
                if not isinstance(de, dict):
                    continue
                try:
                    x = int(de.get("x"))
                    y = int(de.get("y"))
                    d = str(de.get("dir", de.get("edge", "N"))).upper().strip()
                except Exception:
                    continue
                x0 = x * GRID_SIZE
                y0 = y * GRID_SIZE
                if d == "N":
                    x1, y1, x2, y2 = x0, y0, x0 + GRID_SIZE, y0
                elif d == "S":
                    x1, y1, x2, y2 = x0, y0 + GRID_SIZE, x0 + GRID_SIZE, y0 + GRID_SIZE
                elif d == "W":
                    x1, y1, x2, y2 = x0, y0, x0, y0 + GRID_SIZE
                else:  # E
                    x1, y1, x2, y2 = x0 + GRID_SIZE, y0, x0 + GRID_SIZE, y0 + GRID_SIZE

                li = QGraphicsLineItem(x1, y1, x2, y2)
                li.setPen(pen_de)
                li.setZValue(57)  # under half-walls, above terrain
                try:
                    if "drop_ft" in de and de.get("drop_ft") is not None:
                        li.setToolTip(f"Drop edge: {int(de.get('drop_ft') or 0)} ft")
                    else:
                        li.setToolTip("Drop edge")
                except Exception:
                    pass
                scene.addItem(li)
                self._overlay_items.append(li)

# Terrain (light tint)
        terrain = meta.get("terrain", {}) or {}
        pen_t = QPen(QColor(0, 0, 0, 80))  # subtle border; adjust if you want
        for k, tid in terrain.items():
            if not isinstance(k, str):
                continue
            try:
                sx, sy = k.split(",")
                x, y = int(sx), int(sy)
            except Exception:
                continue

            r = QGraphicsRectItem(x * GRID_SIZE, y * GRID_SIZE, GRID_SIZE, GRID_SIZE)
            r.setPen(pen_t)

            col = self._terrain_color(str(tid))
            col.setAlpha(255)       # make the brush solid; opacity handles transparency
            r.setBrush(col)
            r.setOpacity(0.5)      # <<< key (8% opacity)

            r.setZValue(40)         # keep consistent; your 12 is oddly low
            scene.addItem(r)
            self._overlay_items.append(r)

        # ---- Elevation overlay ----
        elevation = meta.get("elevation", {}) or {}
        if isinstance(elevation, dict):
            for key, val in elevation.items():
                try:
                    x_str, y_str = key.split(",")
                    x = int(x_str)
                    y = int(y_str)
                    elev = int(val)
                except Exception:
                    continue

                if elev <= 0:
                    continue

                # Color intensity scales with elevation
                alpha = min(120, 30 + elev * 2)
                color = QColor(120, 80, 200, alpha)

                r = QGraphicsRectItem(
                    x * GRID_SIZE,
                    y * GRID_SIZE,
                    GRID_SIZE,
                    GRID_SIZE
                )
                r.setBrush(QBrush(color))
                r.setPen(QColor(0, 0, 0, 0))
                r.setZValue(40)

                try:
                    r.setToolTip(f"Elevation: {elev} ft")
                except Exception:
                    pass

                scene.addItem(r)
                self._overlay_items.append(r)
    
        # Hazards (tint, per-type)
        hazards = meta.get("hazards", []) or []

        # Theme-forward but distinct colors. Brush alpha is applied via item opacity below.
        HAZARD_COLORS = {
            "fire": QColor(255, 80, 0, 255),
            "acid": QColor(60, 220, 60, 255),
            "poison": QColor(140, 80, 200, 255),
            "cold": QColor(0, 190, 255, 255),
            "electric": QColor(255, 220, 0, 255),
            "lightning": QColor(255, 220, 0, 255),
            "necrotic": QColor(80, 80, 80, 255),
            "radiant": QColor(255, 210, 120, 255),
            "pit": QColor(30, 30, 30, 255),
            "spikes": QColor(170, 170, 170, 255),
            "gas": QColor(120, 180, 140, 255),
            "smoke": QColor(120, 120, 120, 255),
        }

        for h in hazards:
            if not isinstance(h, dict):
                continue
            try:
                x = int(h.get("x"))
                y = int(h.get("y"))
            except Exception:
                continue

            htype = str(h.get("hazard_type") or "").strip().lower()

            base = HAZARD_COLORS.get(htype)
            if base is None:
                # Deterministic fallback: hash the type into a visible-ish RGB range
                hh = abs(hash(htype)) if htype else 0
                r0 = 60 + (hh % 160)
                g0 = 60 + ((hh // 7) % 160)
                b0 = 60 + ((hh // 49) % 160)
                base = QColor(int(r0), int(g0), int(b0), 255)

            # Border is slightly more opaque than fill for readability
            pen_h = QPen(QColor(base.red(), base.green(), base.blue(), 190))
            r = QGraphicsRectItem(x * GRID_SIZE, y * GRID_SIZE, GRID_SIZE, GRID_SIZE)
            r.setPen(pen_h)
            r.setBrush(QColor(base.red(), base.green(), base.blue(), 255))
            r.setOpacity(0.18)
            r.setZValue(45)
            scene.addItem(r)
            self._overlay_items.append(r)

    # ---------- Click dispatch ----------
    def _handle_map_click(self, scene_x: float, scene_y: float, modifiers: int):
        # Lazily load if needed
        if not self._ensure_meta_loaded(force=False):
            return

        meta = getattr(self.main_window, "current_map_meta", None) or {}
        events = []

        try:
            if self.mode == "Walls":
                edge = pick_edge(scene_x, scene_y, GRID_SIZE)
                if edge is None:
                    return
                x, y, d = edge
                walls = meta.setdefault("walls", [])

                if self._erase_mode:
                    walls[:] = [
                        w for w in walls
                        if not (
                            isinstance(w, dict)
                            and int(w.get("x", -999)) == x
                            and int(w.get("y", -999)) == y
                            and str(w.get("dir", "")).upper() == d
                        )
                    ]
                else:
                    # Enforce exclusivity: remove any door or half-wall on this edge.
                    doors = meta.setdefault("doors", [])
                    doors[:] = [
                        dd for dd in doors
                        if not (
                            isinstance(dd, dict)
                            and int(dd.get("x", -999)) == x
                            and int(dd.get("y", -999)) == y
                            and str(dd.get("edge", dd.get("dir", "N"))).upper().strip() == str(d).upper().strip()
                        )
                    ]
                    half_walls = meta.setdefault("half_walls", [])
                    half_walls[:] = [
                        ww for ww in half_walls
                        if not (
                            isinstance(ww, dict)
                            and int(ww.get("x", -999)) == x
                            and int(ww.get("y", -999)) == y
                            and str(ww.get("dir", "")).upper() == d
                        )
                    ]
                    meta, events = toggle_wall(meta, x, y, d)

            elif self.mode == "Doors":
                edge = pick_edge(scene_x, scene_y, GRID_SIZE)
                if edge is None:
                    return
                x, y, d = edge
                doors = meta.setdefault("doors", [])

                if self._erase_mode:
                    doors[:] = [
                        dd for dd in doors
                        if not (
                            isinstance(dd, dict)
                            and int(dd.get("x", -999)) == x
                            and int(dd.get("y", -999)) == y
                            and str(dd.get("edge", dd.get("dir", "N"))).upper().strip() == str(d).upper().strip()
                        )
                    ]
                else:
                    # Enforce exclusivity: remove any full wall or half-wall on this edge.
                    walls = meta.setdefault("walls", [])
                    walls[:] = [
                        w for w in walls
                        if not (
                            isinstance(w, dict)
                            and int(w.get("x", -999)) == x
                            and int(w.get("y", -999)) == y
                            and str(w.get("dir", "")).upper() == d
                        )
                    ]
                    half_walls = meta.setdefault("half_walls", [])
                    half_walls[:] = [
                        ww for ww in half_walls
                        if not (
                            isinstance(ww, dict)
                            and int(ww.get("x", -999)) == x
                            and int(ww.get("y", -999)) == y
                            and str(ww.get("dir", "")).upper() == d
                        )
                    ]

                    # Upsert door edge. Default closed.
                    found = None
                    for dd in doors:
                        if not isinstance(dd, dict):
                            continue
                        if (int(dd.get("x", -999)) == x
                                and int(dd.get("y", -999)) == y
                                and str(dd.get("edge", dd.get("dir", "N"))).upper().strip() == str(d).upper().strip()):
                            found = dd
                            break

                    if found is None:
                        doors.append({
                            "id": f"D_{x}_{y}_{str(d).upper()}",
                            "x": int(x),
                            "y": int(y),
                            "edge": str(d).upper(),
                            "is_open": False
                        })
                    else:
                        if "is_open" not in found:
                            found["is_open"] = False
                        if "id" not in found or not found.get("id"):
                            found["id"] = f"D_{x}_{y}_{str(d).upper()}"
                        found["edge"] = str(d).upper()
                        found["x"] = int(x)
                        found["y"] = int(y)

            elif self.mode == "Half Walls":
                edge = pick_edge(scene_x, scene_y, GRID_SIZE)
                if edge is None:
                    return
                x, y, d = edge
                half_walls = meta.setdefault("half_walls", [])

                if self._erase_mode:
                    half_walls[:] = [
                        ww for ww in half_walls
                        if not (
                            isinstance(ww, dict)
                            and int(ww.get("x", -999)) == x
                            and int(ww.get("y", -999)) == y
                            and str(ww.get("dir", ww.get("edge", ""))).upper() == d
                        )
                    ]
                else:
                    # Enforce exclusivity: remove any full wall or door on this edge.
                    walls = meta.setdefault("walls", [])
                    walls[:] = [
                        w for w in walls
                        if not (
                            isinstance(w, dict)
                            and int(w.get("x", -999)) == x
                            and int(w.get("y", -999)) == y
                            and str(w.get("dir", "")).upper() == d
                        )
                    ]
                    doors = meta.setdefault("doors", [])
                    doors[:] = [
                        dd for dd in doors
                        if not (
                            isinstance(dd, dict)
                            and int(dd.get("x", -999)) == x
                            and int(dd.get("y", -999)) == y
                            and str(dd.get("edge", dd.get("dir", "N"))).upper().strip() == str(d).upper().strip()
                        )
                    ]

                    # Toggle half wall
                    removed = False
                    for i, ww in enumerate(list(half_walls)):
                        try:
                            if (isinstance(ww, dict)
                                    and int(ww.get("x", -999)) == x
                                    and int(ww.get("y", -999)) == y
                                    and str(ww.get("dir", ww.get("edge", ""))).upper() == d):
                                del half_walls[i]
                                removed = True
                                break
                        except Exception:
                            continue
                    if not removed:
                        half_walls.append({"x": int(x), "y": int(y), "dir": str(d).upper()})

            elif self.mode == "Blocked":
                cell = pick_cell(scene_x, scene_y, GRID_SIZE)
                if cell is None:
                    return
                x, y = cell
                blocked = meta.setdefault("blocked", [])

                if self._erase_mode:
                    blocked[:] = [
                        b for b in blocked
                        if not (isinstance(b, (list, tuple)) and len(b) >= 2 and int(b[0]) == x and int(b[1]) == y)
                    ]
                else:
                    removed = False
                    for i, b in enumerate(list(blocked)):
                        try:
                            if int(b[0]) == x and int(b[1]) == y:
                                del blocked[i]
                                removed = True
                                break
                        except Exception:
                            continue
                    if not removed:
                        blocked.append([x, y])

            elif self.mode == "Terrain":
                cell = pick_cell(scene_x, scene_y, GRID_SIZE)
                if cell is None:
                    return
                x, y = cell
                key = f"{x},{y}"
                terrain = meta.setdefault("terrain", {})

                if self._erase_mode:
                    terrain.pop(key, None)
                else:
                    terrain[key] = self._terrain_id

            elif self.mode == "Lighting":
                cell = pick_cell(scene_x, scene_y, GRID_SIZE)
                if cell is None:
                    return
                x, y = cell
                key = f"{x},{y}"
                lighting = meta.setdefault("lighting", {})
                if not isinstance(lighting, dict):
                    lighting = {}
                    meta["lighting"] = lighting

                if self._erase_mode:
                    lighting.pop(key, None)
                else:
                    try:
                        lvl = str(self.lighting_combo.currentText() or "bright").strip().lower()
                    except Exception:
                        lvl = "bright"
                    if lvl not in {"bright", "dim", "dark", "magical_dark"}:
                        lvl = "bright"
                    lighting[key] = lvl

            
            elif self.mode == "Elevation":
                cell = pick_cell(scene_x, scene_y, GRID_SIZE)
                if cell is None:
                    return
                x, y = cell
                key = f"{int(x)},{int(y)}"
                elev = meta.setdefault("elevation", {})
                if not isinstance(elev, dict):
                    elev = {}
                    meta["elevation"] = elev

                if self._erase_mode:
                    elev.pop(key, None)
                else:
                    try:
                        ft = int(self.elevation_spin.value())
                    except Exception:
                        ft = 0
                    elev[key] = int(ft)

            
            elif self.mode == "Drop Edges":
                edge = pick_edge(scene_x, scene_y, GRID_SIZE)
                if edge is None:
                    return
                x, y, d = edge
                drop_edges = meta.setdefault("drop_edges", [])
                if not isinstance(drop_edges, list):
                    drop_edges = []
                    meta["drop_edges"] = drop_edges

                if self._erase_mode:
                    drop_edges[:] = [
                        de for de in drop_edges
                        if not (
                            isinstance(de, dict)
                            and int(de.get("x", -999)) == int(x)
                            and int(de.get("y", -999)) == int(y)
                            and str(de.get("dir", de.get("edge", "N"))).upper().strip() == str(d).upper().strip()
                        )
                    ]
                else:
                    removed = False
                    for i, de in enumerate(list(drop_edges)):
                        try:
                            if (
                                isinstance(de, dict)
                                and int(de.get("x", -999)) == int(x)
                                and int(de.get("y", -999)) == int(y)
                                and str(de.get("dir", de.get("edge", "N"))).upper().strip() == str(d).upper().strip()
                            ):
                                del drop_edges[i]
                                removed = True
                                break
                        except Exception:
                            continue
                    if not removed:
                        try:
                            df = int(getattr(self, "drop_edge_spin", None).value())
                        except Exception:
                            df = 0
                        payload = {"x": int(x), "y": int(y), "dir": str(d).upper()}
                        if int(df) > 0:
                            payload["drop_ft"] = int(df)
                        drop_edges.append(payload)
            elif self.mode == "Hazards":
                cell = pick_cell(scene_x, scene_y, GRID_SIZE)
                if cell is None:
                    return
                gx, gy = cell

                hazards = meta.setdefault("hazards", [])
                if not isinstance(hazards, list):
                    hazards = []
                    meta["hazards"] = hazards

                # Read fields
                try:
                    htype = str(self.hazard_type_combo.currentText() or "fire").strip().lower()
                except Exception:
                    htype = "fire"
                try:
                    dmg = str(self.hazard_damage_edit.text() or "1").strip()
                except Exception:
                    dmg = "1"
                # Read triggers from checkboxes
                trigs = []
                try:
                    if getattr(self, "haz_enter_cb", None) is not None and self.haz_enter_cb.isChecked():
                        trigs.append("enter")
                    if getattr(self, "haz_turn_start_cb", None) is not None and self.haz_turn_start_cb.isChecked():
                        trigs.append("turn_start")
                    if getattr(self, "haz_turn_end_cb", None) is not None and self.haz_turn_end_cb.isChecked():
                        trigs.append("turn_end")
                except Exception:
                    trigs = []

                if not trigs:
                    trigs = ["enter"]

                # Upsert: one hazard record per cell
                found = None
                for h in hazards:
                    if isinstance(h, dict) and int(h.get("x", -999)) == int(gx) and int(h.get("y", -999)) == int(gy):
                        found = h
                        break

                payload = {
                    "x": int(gx),
                    "y": int(gy),
                    "hazard_type": htype,
                    "damage": dmg,
                    "triggers": list(sorted(set(trigs))),
                    # legacy for compatibility (optional)
                    "trigger": (list(sorted(set(trigs)))[0]),
                }
                try:
                    save_ability = str(self.hazard_save_ability_combo.currentText() or "None").strip().upper()
                except Exception:
                    save_ability = "NONE"
                if save_ability and save_ability != "NONE":
                    payload["save_ability"] = save_ability
                    try:
                        payload["save_dc"] = int(self.hazard_save_dc_spin.value())
                    except Exception:
                        payload["save_dc"] = 10
                    try:
                        payload["save_on_success"] = str(self.hazard_save_success_combo.currentText() or "none").strip().lower()
                    except Exception:
                        payload["save_on_success"] = "none"
                    try:
                        payload["save_mode"] = str(self.hazard_save_mode_combo.currentText() or "normal").strip().lower()
                    except Exception:
                        payload["save_mode"] = "normal"

                if found is None:
                    hazards.append(payload)
                else:
                    found.clear()
                    found.update(payload)

            elif self.mode == "Foliage":
                cell = pick_cell(scene_x, scene_y, GRID_SIZE)
                if cell is None:
                    return
                x, y = cell
                key = f"{x},{y}"

                foliage = meta.setdefault("foliage", {})
                if not isinstance(foliage, dict):
                    foliage = {}
                    meta["foliage"] = foliage

                if self._erase_mode:
                    foliage.pop(key, None)
                else:
                    try:
                        dens = float(str(self.foliage_combo.currentText() or "0.5").strip())
                    except Exception:
                        dens = 0.5
                    dens = max(0.0, min(1.0, float(dens)))
                    foliage[key] = dens

            elif self.mode == "Fog Zones":
                cell = pick_cell(scene_x, scene_y, GRID_SIZE)
                if cell is None:
                    return
                gx, gy = cell

                zones = meta.setdefault("fog_zones", [])
                if not isinstance(zones, list):
                    zones = []
                    meta["fog_zones"] = zones

                if self._erase_mode:
                    # Remove zones centered on this cell.
                    zones[:] = [
                        z for z in zones
                        if not (
                            isinstance(z, dict)
                            and int(float(z.get("cx", -999.0))) == int(gx)
                            and int(float(z.get("cy", -999.0))) == int(gy)
                        )
                    ]
                else:
                    try:
                        r = int(float(str(self.fog_radius_edit.text() or "3").strip()))
                    except Exception:
                        r = 3
                    r = max(1, int(r))
                    try:
                        dens = float(str(self.fog_density_combo.currentText() or "0.5").strip())
                    except Exception:
                        dens = 0.5
                    dens = max(0.0, min(1.0, float(dens)))

                    payload = {
                        "cx": float(gx) + 0.5,
                        "cy": float(gy) + 0.5,
                        "r": float(r),
                        "density": float(dens),
                    }
                    # Upsert by center cell
                    found = None
                    for z in zones:
                        if not isinstance(z, dict):
                            continue
                        try:
                            if int(float(z.get("cx", -999.0))) == int(gx) and int(float(z.get("cy", -999.0))) == int(gy):
                                found = z
                                break
                        except Exception:
                            continue
                    if found is None:
                        zones.append(payload)
                    else:
                        found.clear()
                        found.update(payload)

            # Commit mutated meta back
            self.main_window.current_map_meta = meta

            # Optional: emit local log events if campaign logger exists
            logger = getattr(self.main_window, "campaign_logger", None)
            if logger is not None and hasattr(logger, "log_event"):
                for e in events or []:
                    try:
                        logger.log_event(e)
                    except Exception:
                        pass

            self.redraw_overlays()

        except Exception:
            # Fail-safe: never crash on bad input in editor
            try:
                self.redraw_overlays()
            except Exception:
                pass
        return
    
    def is_painting_enabled(self) -> bool:
        try:
            return bool(self.chk_enable_paint.isChecked())
        except Exception:
            return True

    def _on_enable_paint_toggled(self, checked: bool):
        # Only affects click routing. Does NOT clear overlays.
        self.enable_on_map(bool(checked))

    def _terrain_color(self, terrain_id: str):
        tid = (terrain_id or "").strip().lower()

        # Simple mapping; adjust to taste.
        # (QColor accepts hex strings; alpha handled separately if you already do it)
        if "ice" in tid:
            return QColor("#7fd8ff")   # ice blue
        if "sand" in tid or "desert" in tid:
            return QColor("#f4b183")   # sand orange
        if "difficult" in tid or "mud" in tid:
            return QColor("#a6a6a6")   # gray
        if "water" in tid:
            return QColor("#4aa3ff")
        if "hazard" in tid or "lava" in tid or "fire" in tid:
            return QColor("#ff6b6b")

        return QColor("#c8c8c8")       # default
    
    def _on_erase_toggled(self, checked: bool):
        self._erase_mode = bool(checked)

    def _ensure_hazard_trigger_checkboxes(self, parent_layout):
        """
        Ensures the hazard trigger checkboxes exist as:
        - self.haz_enter_cb
        - self.haz_turn_start_cb
        - self.haz_turn_end_cb

        Call this once when building the Hazards UI section.
        """
        try:
            from PyQt5.QtWidgets import QCheckBox, QHBoxLayout
        except Exception:
            return

        # If already created, do nothing
        if getattr(self, "haz_enter_cb", None) is not None and \
        getattr(self, "haz_turn_start_cb", None) is not None and \
        getattr(self, "haz_turn_end_cb", None) is not None:
            return

        row = QHBoxLayout()

        self.haz_enter_cb = QCheckBox("On Enter")
        self.haz_turn_start_cb = QCheckBox("On Turn Start")
        self.haz_turn_end_cb = QCheckBox("On Turn End")

        # Sensible defaults: enter checked by default
        self.haz_enter_cb.setChecked(True)
        self.haz_turn_start_cb.setChecked(False)
        self.haz_turn_end_cb.setChecked(False)

        row.addWidget(self.haz_enter_cb)
        row.addWidget(self.haz_turn_start_cb)
        row.addWidget(self.haz_turn_end_cb)

        parent_layout.addLayout(row)