import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image
from PySide6 import QtWidgets as W
from PySide6.QtTest import QTest
from dthr.gui import Window
from dthr.core import Studio
from dthr.storage import Store
from test_core import RecordingBackend


def settle(app, win):
    for _ in range(400):
        app.processEvents()
        QTest.qWait(10)
        if not win.active and not win.timer.isActive():
            return
    raise AssertionError("GUI worker did not finish")


def test_editor(tmp_path):
    app = W.QApplication.instance() or W.QApplication([])
    studio = Studio(Store(tmp_path / "data"), RecordingBackend())
    win = Window(studio)
    win.show()
    source = tmp_path / "gradient.png"
    Image.linear_gradient("L").save(source)
    win.set_entry(studio.store.import_image(source)[0])
    settle(app, win)
    assert (
        win.current().name == "LLMBAZ"
        and win.viewer.canvas.original
        and win.viewer.canvas.processed
    )
    first = win.viewer.canvas.processed.toImage()
    win.fields["foreground"].edit.setText("#11ee77")
    win.fields["effect"].setCurrentText("bayer")
    settle(app, win)
    assert win.viewer.canvas.processed.toImage() != first and not studio.backend.calls
    win.viewer.mode.setCurrentText("Split")
    win.viewer.zoom.setCurrentText("2×")
    project = tmp_path / "test.ditherproj"
    win.project = project
    win.save()
    win.new()
    win.open_project(project)
    settle(app, win)
    assert (
        win.viewer.mode.currentText() == "Split"
        and win.value("foreground") == "#11ee77"
    )
    win.fields["foreground"].edit.setText("#badbad")
    settle(app, win)
    win.undo()
    assert win.value("foreground") == "#11ee77"
    win.fields["texture_seed"].setText("broken")
    settle(app, win)
    assert win.logdock.isVisible()
    win.close()
    again = Window(studio)
    assert again.settings.contains("geometry") and str(project) in again.settings.value(
        "recent"
    )
    again.close()


@pytest.mark.parametrize("field", ["foreground", "background"])
def test_color_draft_keeps_preview(tmp_path, field):
    app = W.QApplication.instance() or W.QApplication([])
    studio = Studio(Store(tmp_path / "data"), RecordingBackend())
    win = Window(studio)
    win.show()
    try:
        source = tmp_path / "gradient.png"
        Image.linear_gradient("L").save(source)
        win.set_entry(studio.store.import_image(source)[0])
        settle(app, win)
        widget = win.fields[field]
        initial = widget.color
        before = win.viewer.canvas.processed.toImage()
        serial = win.serial
        widget.edit.setFocus()
        widget.edit.selectAll()
        for char in "#12aB7":
            QTest.keyClicks(widget.edit, char)
            settle(app, win)
            assert win.serial == serial
            assert getattr(win.current().postprocess, field) == initial
            assert win.viewer.canvas.processed.toImage() == before
        QTest.keyClicks(widget.edit, "F")
        settle(app, win)
        assert widget.color == "#12aB7F" and win.serial == serial + 1
        assert win.viewer.canvas.processed.toImage() != before
        widget.edit.setText("#12345x")
        win.fields["seed"].setFocus()
        settle(app, win)
        assert widget.edit.text() == widget.color == "#12aB7F"
        assert not win.log.toPlainText() and not studio.backend.calls
    finally:
        win.close()
