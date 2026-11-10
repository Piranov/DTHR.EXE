import re

from PIL.ImageQt import ImageQt
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
from .schema import RGB_PATTERN


def combo(items):
    box = W.QComboBox()
    box.addItems(items)
    return box


def number(lo, hi, step=1):
    if isinstance(step, float):
        box = W.QDoubleSpinBox()
        box.setDecimals(2)
    else:
        box = W.QSpinBox()
    box.setRange(lo, hi)
    box.setSingleStep(step)
    return box


def pixmap(image):
    return G.QPixmap.fromImage(ImageQt(image.convert("RGBA")))


class ColorInput(W.QWidget):
    changed = C.Signal()

    def __init__(self, initial_color):
        super().__init__()
        row = W.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.swatch = W.QPushButton()
        self.swatch.setFixedWidth(30)
        self.color = initial_color
        self.edit = W.QLineEdit(initial_color)
        self.edit.setToolTip("Enter a six-digit RGB color, for example #d8d0c0")
        self.swatch.setStyleSheet(f"background:{self.color};")
        row.addWidget(self.swatch)
        row.addWidget(self.edit)
        self.swatch.clicked.connect(self.pick)
        self.edit.textChanged.connect(self.update_color)
        self.edit.editingFinished.connect(self.finish_edit)

    def update_color(self, text):
        if text == self.color or not re.fullmatch(RGB_PATTERN, text):
            return
        self.color = text
        self.swatch.setStyleSheet(f"background:{text};")
        self.changed.emit()

    def finish_edit(self):
        if self.edit.text() != self.color:
            self.edit.setText(self.color)

    def pick(self):
        color = W.QColorDialog.getColor(
            G.QColor(self.color), self, "Choose RGB color"
        )
        if color.isValid():
            self.edit.setText(color.name())


class Canvas(W.QWidget):
    def __init__(self):
        super().__init__()
        self.original = self.processed = self.aligned = self.gray = None
        self.mode = "Processed"
        self.checker = False
        self.split = 0.5
        self.setSizePolicy(
            W.QSizePolicy.Policy.Expanding, W.QSizePolicy.Policy.Expanding
        )

    def display_image(self):
        if self.mode == "Original" or not self.processed:
            return self.original
        return self.gray if self.mode == "Luminance" else self.processed

    def paintEvent(self, event):
        painter = G.QPainter(self)
        painter.fillRect(self.rect(), G.QColor("#181b1b"))
        if self.original is None:
            painter.setPen(G.QColor("#a5aaa4"))
            painter.drawText(
                self.rect(), C.Qt.AlignCenter, "Import an image or generate an original"
            )
            return
        image = self.display_image()
        size = image.size().scaled(self.size(), C.Qt.KeepAspectRatio)
        rect = C.QRect(C.QPoint(0, 0), size)
        rect.moveCenter(self.rect().center())
        if self.checker and self.mode == "Original":
            painter.save()
            painter.setClipRect(rect)
            for y in range(rect.top(), rect.bottom() + 1, 12):
                for x in range(rect.left(), rect.right() + 1, 12):
                    painter.fillRect(
                        x,
                        y,
                        12,
                        12,
                        G.QColor(
                            "#81817b"
                            if ((x - rect.left()) // 12 + (y - rect.top()) // 12) % 2
                            else "#bcbcb7"
                        ),
                    )
            painter.restore()
        painter.setRenderHint(G.QPainter.SmoothPixmapTransform, False)
        painter.drawPixmap(rect, image)
        if self.mode == "Split" and self.aligned:
            boundary = rect.left() + round(rect.width() * self.split)
            painter.setClipRect(
                C.QRect(rect.left(), rect.top(), boundary - rect.left(), rect.height())
            )
            painter.drawPixmap(rect, self.aligned)
            painter.setClipping(False)
            painter.setPen(G.QColor("#f5a875"))
            painter.drawLine(boundary, rect.top(), boundary, rect.bottom())
        self.paint_rect = rect

    def mouseMoveEvent(self, event):
        if (
            self.mode == "Split"
            and hasattr(self, "paint_rect")
            and event.buttons() & C.Qt.LeftButton
        ):
            self.split = max(
                0,
                min(
                    1,
                    (event.position().x() - self.paint_rect.x())
                    / max(1, self.paint_rect.width()),
                ),
            )
            self.update()


class Viewer(W.QWidget):
    def __init__(self):
        super().__init__()
        layout = W.QVBoxLayout(self)
        bar = W.QHBoxLayout()
        self.mode = combo(["Processed", "Original", "Split", "Luminance"])
        self.zoom = combo(["Fit", "100%", "2×", "4×", "8×"])
        bar.addWidget(self.mode)
        bar.addStretch()
        bar.addWidget(self.zoom)
        layout.addLayout(bar)
        self.area = W.QScrollArea()
        self.area.setAlignment(C.Qt.AlignCenter)
        self.canvas = Canvas()
        self.area.setWidget(self.canvas)
        self.overlay = W.QLabel(self.area.viewport())
        self.overlay.setStyleSheet("background:#303832;color:#e4eed6;padding:6px 12px")
        self.overlay.setGeometry(10, 10, 200, 32)
        self.overlay.hide()
        layout.addWidget(self.area)
        self.status = W.QLabel("No image loaded")
        layout.addWidget(self.status)
        self.zoom.currentTextChanged.connect(self.refresh)
        self.mode.currentTextChanged.connect(self.refresh)
        self.refresh()

    def refresh(self):
        canvas = self.canvas
        canvas.mode = self.mode.currentText()
        factor = [0, 1, 2, 4, 8][self.zoom.currentIndex()]
        pix = canvas.display_image()
        self.area.setWidgetResizable(not factor or not pix)
        if factor and pix:
            canvas.resize(pix.size() * factor)
        canvas.update()
