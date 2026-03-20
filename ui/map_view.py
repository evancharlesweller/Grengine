from PyQt5.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPathItem
from PyQt5.QtGui import QPen, QPainterPath, QColor
from PyQt5.QtCore import Qt, QPointF

class MapView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.drawing_enabled = False  # Drawing toggle
        self.drawing = False
        self.current_path = None
        self.pen_color = QColor("black")
        self.pen_width = 2
        self.erase_mode = False

        # Map metadata editor dispatch (walls/blocked/terrain)
        self.meta_edit_enabled = False
        self.meta_click_handler = None  # callable(scene_x:float, scene_y:float, modifiers:int) -> None

    def set_drawing_enabled(self, enabled: bool):
        self.drawing_enabled = enabled

    def set_pen_color(self, color):
        self.pen_color = QColor(color)
        self.erase_mode = False

    def set_erase_mode(self):
        self.erase_mode = True

    def clear_drawings(self):
        for item in self.scene().items():
            if isinstance(item, QGraphicsPathItem):
                self.scene().removeItem(item)

    def set_meta_edit_enabled(self, enabled: bool, handler=None):
        self.meta_edit_enabled = bool(enabled)
        self.meta_click_handler = handler

    def mousePressEvent(self, event):
        if self.meta_edit_enabled and event.button() == Qt.LeftButton and callable(self.meta_click_handler):
            p = self.mapToScene(event.pos())
            self.meta_click_handler(float(p.x()), float(p.y()), int(event.modifiers()))
            event.accept()
            return
        if self.drawing_enabled and event.button() == Qt.LeftButton:
            self.drawing = True
            self.current_path = QPainterPath(self.mapToScene(event.pos()))
            path_item = QGraphicsPathItem(self.current_path)

            pen = QPen(Qt.transparent if self.erase_mode else self.pen_color)
            pen.setWidth(20 if self.erase_mode else self.pen_width)
            path_item.setPen(pen)

            self.scene().addItem(path_item)
            self.last_path_item = path_item
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drawing_enabled and self.drawing and self.current_path:
            self.current_path.lineTo(self.mapToScene(event.pos()))
            self.last_path_item.setPath(self.current_path)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drawing_enabled and event.button() == Qt.LeftButton:
            self.drawing = False
            self.current_path = None
        else:
            super().mouseReleaseEvent(event)