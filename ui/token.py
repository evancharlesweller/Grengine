from PyQt5.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem, QMenu
from PyQt5.QtGui import QPixmap, QPainterPath, QPainter, QCursor, QPen, QColor, QBrush
from PyQt5.QtCore import QRectF, Qt
import uuid
from PyQt5.QtWidgets import QGraphicsItem
from ui.constants import GRID_SIZE, TOKEN_SCALE


class DraggableToken(QGraphicsPixmapItem):
    def __init__(self, pixmap, grid_size=GRID_SIZE, movement=30, weapon_ref=None):
        """
        weapon_ref can be either:
        - weapon_id (preferred)
        - weapon name (legacy)
        """
        token_px = int(grid_size * TOKEN_SCALE)

        scaled = pixmap.scaled(
            token_px, token_px,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation
        )

        rounded = QPixmap(token_px, token_px)
        rounded.fill(Qt.transparent)

        path = QPainterPath()
        path.addEllipse(0, 0, token_px, token_px)

        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, scaled)
        painter.end()

        super().__init__(rounded)

        self.setFlags(
            QGraphicsPixmapItem.ItemIsMovable |
            QGraphicsPixmapItem.ItemIsSelectable
        )
        self.setCursor(QCursor(Qt.OpenHandCursor))

        # Identity / core stats
        self.grid_size = grid_size
        self.token_id = str(uuid.uuid4())
        self.display_name = "Unnamed"
        self.hp = 10
        self.max_hp = 10
        self.ac = 10
        self.movement = movement
        self.attack_modifier = 0

        # Equipment:
        # Keep BOTH id + legacy fields to support gradual migration.
        self.weapon_id = ""   # preferred
        self.armor_id = ""    # preferred
        self.weapon = (weapon_ref or "").strip()  # may be id OR name
        self.armor = ""

        self.filepath = None

        # Faction/side (used for HP bar color)
        self.side = "enemy"  # player/enemy/ally/neutral
        self.vision_ft = 60

        # B-X4: Vision Types / senses (optional)
        self.vision_type = "normal"
        self.darkvision_ft = 0
        self.blindsight_ft = 0
        self.truesight_ft = 0
        self.tremorsense_ft = 0
        self.devils_sight_ft = 0

        self.on_moved_callback = None     # callable(token_id, from_gx, from_gy, to_gx, to_gy)

        # If this token is still selected after moving, redraw overlays so they stay centered.
        # (This matches the stable Phase 5.0 behavior: no need to deselect/reselect.)
        try:
            if self.isSelected():
                sc = self.scene()
                if sc is not None:
                    # movement range
                    try:
                        self.show_movement_range(sc)
                    except Exception:
                        pass
                    # attack range if we have cached weapon data from selection
                    wd = getattr(self, "_cached_weapon_data", None)
                    if isinstance(wd, dict) and wd:
                        try:
                            self.show_attack_range(sc, wd)
                        except Exception:
                            pass
        except Exception:
            pass

        self._drag_from_grid = None       # (gx, gy)
        self._suppress_move_callback = False

        self.movement_indicators = []
        self.attack_indicators = []

        self.update_hp_bar()

    def mousePressEvent(self, event):
        # Do NOT consume right-click here; MainWindow/View owns the context menu.
        # But we DO capture the drag origin for left-drag movement determinism.
        try:
            from PyQt5.QtCore import Qt
            if event.button() == Qt.LeftButton:
                grid_size = self.grid_size
                center_x = self.x() + self.pixmap().width() / 2
                center_y = self.y() + self.pixmap().height() / 2
                from_gx = int(center_x // grid_size)
                from_gy = int(center_y // grid_size)
                self._drag_from_grid = (from_gx, from_gy)
        except Exception:
            pass

        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        # Snap token to grid
        pos = self.pos()
        snapped_x = round(pos.x() / self.grid_size) * self.grid_size + self.grid_size / 2
        snapped_y = round(pos.y() / self.grid_size) * self.grid_size + self.grid_size / 2
        half_width = self.pixmap().width() / 2
        half_height = self.pixmap().height() / 2
        self.setPos(snapped_x - half_width, snapped_y - half_height)

        super().mouseReleaseEvent(event)

        # Notify MainWindow AFTER snap
        if (not self._suppress_move_callback) and callable(getattr(self, "on_moved_callback", None)):
            try:
                grid_size = self.grid_size
                center_x = self.x() + self.pixmap().width() / 2
                center_y = self.y() + self.pixmap().height() / 2
                to_gx = int(center_x // grid_size)
                to_gy = int(center_y // grid_size)

                if self._drag_from_grid is not None:
                    from_gx, from_gy = self._drag_from_grid
                    if (from_gx, from_gy) != (to_gx, to_gy):
                        self.on_moved_callback(
                            self,
                            self.token_id,
                            from_gx, from_gy,
                            to_gx, to_gy
                        )

            except Exception:
                pass

        self._drag_from_grid = None
        self._suppress_move_callback = False

    def contextMenuEvent(self, event):
        # Let MainWindow's view context menu handle right-clicks
        try:
            event.ignore()
        except Exception:
            pass

    def show_movement_range(
        self,
        scene,
        *,
        is_player_view: bool = False,
        only_if_active: bool = False,
        reachable_cells=None,
    ):
        """
        Movement overlay rules:
        - Never steals mouse input (right-click etc.)
        - Player View: show only for PCs (hide NPC ranges)
        - Combat gating: optionally show only if this token is the current active token
        """
        self.hide_movement_range(scene)

        # --- Player View filtering (PC only) ---
        if is_player_view:
            # Prefer your real token fields (DraggableToken has `side`)
            is_pc = (getattr(self, "side", "") == "player") or (getattr(self, "kind", "") == "pc")
            if not is_pc:
                return

        # --- Turn-based gating (only active token shows movement) ---
        if only_if_active:
            if not bool(getattr(self, "is_current_turn", False)):
                return

        grid_size = int(getattr(self, "grid_size", 50))
        move_ft = float(getattr(self, "movement", 0))
        squares = int(move_ft // 5)
        if squares <= 0:
            return

        # Find current grid cell from token center
        center_x = self.x() + (self.pixmap().width() / 2)
        center_y = self.y() + (self.pixmap().height() / 2)
        col = int(center_x // grid_size)
        row = int(center_y // grid_size)

        scene_rect = scene.sceneRect()

        # Compute which cells to draw (render-only).
        # If reachable_cells is provided, we draw exactly those cells.
        # Otherwise we fall back to a simple radius circle (legacy behavior).
        if reachable_cells is not None:
            cells_iter = list(reachable_cells)
        else:
            cells_iter = []
            # Legacy circular overlay (euclidean in squares)
            for dx in range(-squares, squares + 1):
                for dy in range(-squares, squares + 1):
                    if (dx * dx + dy * dy) > (squares * squares):
                        continue
                    cells_iter.append((col + dx, row + dy))

        # Draw within scene bounds
        for (cx, cy) in cells_iter:
            x = int(cx) * grid_size
            y = int(cy) * grid_size

            if not scene_rect.contains(QRectF(x, y, grid_size, grid_size)):
                continue

            rect = QGraphicsRectItem(x, y, grid_size, grid_size)

            # Never let overlays steal right-click/selection/hover
            rect.setAcceptedMouseButtons(Qt.NoButton)
            rect.setAcceptHoverEvents(False)
            rect.setFlag(QGraphicsRectItem.ItemIsSelectable, False)
            rect.setFlag(QGraphicsRectItem.ItemIsMovable, False)
            rect.setFlag(QGraphicsRectItem.ItemIsFocusable, False)

            rect.setPen(QPen(QColor("blue"), 2))
            rect.setBrush(QBrush(QColor(0, 120, 255, 50)))
            rect.setZValue(20)

            scene.addItem(rect)
            self.movement_indicators.append(rect)

    def hide_movement_range(self, scene):
        # Be robust: overlay items may already be deleted or detached from the scene.
        for item in list(self.movement_indicators):
            try:
                if item is None:
                    continue
                sc = item.scene()
                if sc is not None:
                    sc.removeItem(item)
            except Exception:
                pass
        self.movement_indicators.clear()

    def hide_attack_range(self, scene):
        for item in list(self.attack_indicators):
            try:
                if item is None:
                    continue
                sc = item.scene()
                if sc is not None:
                    sc.removeItem(item)
            except Exception:
                pass
        self.attack_indicators.clear()

    def show_attack_range(self, scene, weapon_data: dict):
        """
        weapon_data expected fields (best-effort):
        - type: "melee" or "ranged"
        - range: in feet (int) OR "30ft" string
        """
        self.hide_attack_range(scene)
        cell_size = self.grid_size

        center_x = self.x() + self.pixmap().width() / 2
        center_y = self.y() + self.pixmap().height() / 2
        col = int(center_x // cell_size)
        row = int(center_y // cell_size)

        weapon_type = str(weapon_data.get("type", "melee")).lower().strip()

        raw_range = weapon_data.get("range", 5)
        if isinstance(raw_range, str):
            s = raw_range.lower().replace("ft", "").strip()
            try:
                weapon_range = int(float(s))
            except Exception:
                weapon_range = 5
        else:
            try:
                weapon_range = int(raw_range)
            except Exception:
                weapon_range = 5

        squares = max(1, int(weapon_range // 5))

        range_squares = []

        if weapon_type == "melee":
            offsets = [
                (-1, -1), (-1, 0), (-1, 1),
                (0, -1),           (0, 1),
                (1, -1),  (1, 0),  (1, 1)
            ]
            range_squares = [(col + dx, row + dy) for dx, dy in offsets]

        elif weapon_type == "ranged":
            for dx in range(-squares, squares + 1):
                for dy in range(-squares, squares + 1):
                    if (dx * dx + dy * dy) <= (squares * squares):
                        range_squares.append((col + dx, row + dy))
        else:
            # Unknown type: default to melee adjacency
            offsets = [
                (-1, -1), (-1, 0), (-1, 1),
                (0, -1),           (0, 1),
                (1, -1),  (1, 0),  (1, 1)
            ]
            range_squares = [(col + dx, row + dy) for dx, dy in offsets]

        pen = QPen(Qt.red)
        pen.setWidth(2)
        scene_rect = scene.sceneRect()

        for cx, cy in range_squares:
            x = cx * cell_size
            y = cy * cell_size
            if scene_rect.contains(QRectF(x, y, cell_size, cell_size)):
                rect = QGraphicsRectItem(x, y, cell_size, cell_size)
                # Don't let overlay tiles steal right-click / selection
                rect.setAcceptedMouseButtons(Qt.NoButton)
                rect.setAcceptHoverEvents(False)
                rect.setFlag(QGraphicsRectItem.ItemIsSelectable, False)
                rect.setPen(pen)
                rect.setBrush(QBrush(Qt.NoBrush))
                rect.setPen(QPen(QColor("red"), 2))
                rect.setBrush(QBrush(Qt.NoBrush))
                rect.setZValue(20)
                scene.addItem(rect)
                self.attack_indicators.append(rect)

    def _hp_color(self) -> QColor:
        side = getattr(self, "side", "enemy")
        if side == "player":
            return QColor(0, 200, 0)     # green
        if side == "ally":
            return QColor(0, 120, 255)   # blue
        if side == "enemy":
            return QColor(220, 0, 0)     # red
        return QColor(160, 160, 160)     # neutral/unknown

    def _ensure_hp_bar_items(self):
        if hasattr(self, "_hp_bg") and hasattr(self, "_hp_fill"):
            return

        bar_w = self.pixmap().width()
        bar_h = max(5, int(self.pixmap().height() * 0.12))
        y = -bar_h - 2

        self._hp_bg = QGraphicsRectItem(0, y, bar_w, bar_h, self)
        self._hp_bg.setPen(QPen(QColor(0, 0, 0), 1))
        self._hp_bg.setBrush(QBrush(QColor(0, 0, 0, 180)))
        self._hp_bg.setZValue(1000)

        self._hp_fill = QGraphicsRectItem(0, y, bar_w, bar_h, self)
        self._hp_fill.setPen(QPen(Qt.NoPen))
        self._hp_fill.setBrush(QBrush(self._hp_color()))
        self._hp_fill.setZValue(1001)

    def update_hp_bar(self):
        self._ensure_hp_bar_items()

        max_hp = max(1, int(getattr(self, "max_hp", 1)))
        hp = int(getattr(self, "hp", max_hp))
        hp = max(0, min(hp, max_hp))

        bar_w = self.pixmap().width()
        bar_h = self._hp_bg.rect().height()
        y = self._hp_bg.rect().y()

        pct = hp / max_hp
        fill_w = int(bar_w * pct)

        self._hp_bg.setRect(0, y, bar_w, bar_h)
        self._hp_fill.setRect(0, y, fill_w, bar_h)
        self._hp_fill.setBrush(QBrush(self._hp_color()))

    def set_active_highlight(self, active: bool) -> None:
        """
        DraggableToken is a pixmap item, so it doesn't support setPen().
        We attach a child QGraphicsRectItem as an outline.
        """
        # lazily create outline rect once
        if not hasattr(self, "_active_outline") or self._active_outline is None:
            rect = self.boundingRect()
            self._active_outline = QGraphicsRectItem(rect, self)
            self._active_outline.setZValue(9999)  # ensure it draws above token
            self._active_outline.setBrush(Qt.NoBrush)

            # Do not let the outline steal mouse interactions
            self._active_outline.setAcceptedMouseButtons(Qt.NoButton)
            self._active_outline.setAcceptHoverEvents(False)
            self._active_outline.setFlag(QGraphicsRectItem.ItemIsSelectable, False)

            # default off
            self._active_outline.setPen(QPen(Qt.NoPen))
            self._active_outline.setVisible(False)

        # keep outline rect synced if pixmap changes size
        try:
            self._active_outline.setRect(self.boundingRect())
        except Exception:
            pass

        if active:
            self._active_outline.setVisible(True)
            self._active_outline.setPen(QPen(QColor(255, 215, 0), 3))
        else:
            self._active_outline.setVisible(False)
            self._active_outline.setPen(QPen(Qt.NoPen))