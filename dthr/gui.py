import json
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
from .core import Studio
from .effects import fitted, luminance, quantize
from .models import CATALOG, hardware, install, model_info, uninstall, versions
from . import __version__, runtime
from .schema import Preset, Processing
from .storage import Store, atomic, read_json, remember_root
from .widgets import ColorInput, Viewer, combo, number, pixmap

STYLE = """
QWidget {background:#272b2a;color:#e3e3d9;font:10pt 'Segoe UI';}
QMenuBar,QMenu,QStatusBar {background:#202423;}
QLineEdit,QTextEdit,QPlainTextEdit,QComboBox,QSpinBox,QDoubleSpinBox,QListWidget {background:#1c201f;border:1px solid #49514a;padding:3px;}
QPushButton,QToolButton {background:#353b37;border:1px solid #555e54;padding:5px 9px;}
QPushButton:hover,QToolButton:hover {background:#455044;}
QPushButton:disabled {color:#81857d;}
QPushButton#primary {background:#c7d9a4;color:#192218;font-weight:bold;}
QLabel#section {font:bold 15pt 'Segoe UI';color:#edece2;padding-bottom:8px;}
QSplitter::handle {background:#434943;}
QProgressBar::chunk {background:#a1bb81;}
"""


class Bus(C.QObject):
    finished = C.Signal(object)
    progress = C.Signal(str, float)


class Window(W.QMainWindow):
    def __init__(self, studio):
        super().__init__()
        self.studio, self.store = studio, studio.store
        self.cpu = ThreadPoolExecutor(2, "studio-edit")
        self.gpu = ThreadPoolExecutor(1, "studio-model")
        self.bus = Bus()
        self.bus.finished.connect(self.completed)
        self.bus.progress.connect(self.progress)
        self.active = 0
        self.closing = self.applying = self.preview_busy = False
        self.cancel_event = threading.Event()
        self.entry = self.source = self.project = None
        self.serial = 0
        self.history, self.future = [], []
        self.fields = {}
        self.settings = C.QSettings(
            str(self.store.root / "desktop.ini"), C.QSettings.IniFormat
        )
        self.setWindowTitle("DTHR.EXE")
        self.resize(1280, 820)
        self.split = W.QSplitter()
        self.viewer = Viewer()
        self.left = self.panel("Generation")
        self.split.addWidget(self.viewer)
        self.right = self.panel("Post-process")
        self.setCentralWidget(self.split)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([290, 640, 300])
        self.timer = C.QTimer(self, interval=50, singleShot=True)
        self.timer.timeout.connect(self.render)
        self.build_generation()
        self.build_processing()
        self.logdock = W.QDockWidget("Activity", self)
        self.log = W.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.logdock.setWidget(self.log)
        self.addDockWidget(C.Qt.BottomDockWidgetArea, self.logdock)
        self.logdock.hide()
        self.menus()
        self.apply(self.store.preset())
        selected = self.settings.value("model")
        if selected in CATALOG:
            self.fields["model"].setCurrentText(selected)
        for key, restore in [
            ("geometry", self.restoreGeometry),
            ("split", self.split.restoreState),
        ]:
            if self.settings.contains(key):
                restore(self.settings.value(key))
        self.statusBar().showMessage(str(self.store.root))

    def panel(self, title):
        scroll = W.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(225)
        pane = W.QWidget()
        layout = W.QVBoxLayout(pane)
        label = W.QLabel(title, objectName="section")
        layout.addWidget(label)
        form = W.QFormLayout()
        form.setFieldGrowthPolicy(W.QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)
        layout.addStretch()
        scroll.setWidget(pane)
        self.split.addWidget(scroll)
        return form

    def field(self, form, key, label, widget):
        self.fields[key] = widget
        form.addRow(label, widget)
        if form == self.right or key in {"width", "height"}:
            if isinstance(widget, ColorInput):
                signal = widget.changed
            elif isinstance(widget, W.QComboBox):
                signal = widget.currentTextChanged
            elif isinstance(widget, W.QCheckBox):
                signal = widget.toggled
            elif isinstance(widget, W.QLineEdit):
                signal = widget.textChanged
            else:
                signal = widget.valueChanged
            signal.connect(self.edited)
        return widget

    def button(self, form, text, action, primary=False):
        b = W.QPushButton(text)
        if primary:
            b.setObjectName("primary")
        b.clicked.connect(lambda: self.guard(action))
        form.addRow(b)
        return b

    def build_generation(self):
        f = self.left
        self.field(f, "model", "Model", combo(CATALOG)).currentTextChanged.connect(
            self.capabilities
        )
        self.model_note = W.QLabel()
        self.model_note.setWordWrap(True)
        f.addRow(self.model_note)
        self.field(f, "width", "Output width", number(1, 4096))
        self.field(f, "height", "Output height", number(1, 4096))
        self.strategy = combo(["Auto", "Manual"])
        f.addRow("Generation size", self.strategy)
        self.field(f, "gen_width", "Internal width", number(256, 2048, 16))
        self.field(f, "gen_height", "Internal height", number(256, 2048, 16))
        self.strategy.currentTextChanged.connect(self.resolution)
        for key, label in [
            ("prompt", "Prompt"),
            ("negative_prompt", "Negative prompt"),
        ]:
            edit = W.QPlainTextEdit()
            edit.setMaximumHeight(80)
            self.field(f, key, label, edit)
        self.field(f, "seed", "Seed", W.QLineEdit())
        self.button(
            f,
            "Randomize seed",
            lambda: self.fields["seed"].setText(str(secrets.randbits(63))),
        )
        self.field(f, "steps", "Steps", number(1, 150))
        self.field(f, "guidance", "Guidance", number(1, 30, 0.1))
        self.field(f, "scheduler", "Scheduler", combo(["default", "euler", "dpm++"]))
        self.field(f, "batch", "Batch", number(1, 8))
        self.field(f, "device", "Device", combo(["cuda", "offload", "cpu"]))
        self.generate_btn = self.button(f, "Generate", self.generate, True)
        self.cancel_btn = self.button(f, "Cancel generation", self.cancel_event.set)
        self.cancel_btn.setEnabled(False)
        self.bar = W.QProgressBar()
        self.bar.setRange(0, 100)
        f.addRow(self.bar)

    def build_processing(self):
        f = self.right
        self.field(
            f,
            "effect",
            "Effect",
            combo(["pixel", "floyd", "bayer", "threshold", "grunge"]),
        )
        slider = W.QSlider(C.Qt.Horizontal)
        slider.setRange(0, 100)
        self.field(f, "strength", "Strength", slider)
        self.strength_value = W.QLabel()
        slider.valueChanged.connect(lambda n: self.strength_value.setText(f"{n}%"))
        f.addRow("", self.strength_value)
        defaults = Processing()
        for k, lab in [("foreground", "Ink"), ("background", "Paper")]:
            self.field(f, k, lab, ColorInput(getattr(defaults, k)))
        self.button(f, "Swap colors", self.swap)
        self.field(f, "fit", "Fitting", combo(["auto", "cover", "contain"]))
        self.field(f, "block_size", "Pixel block", number(0, 128)).setSpecialValueText(
            "From strength"
        )
        self.field(f, "matrix", "Bayer matrix", combo(["2", "4", "8"]))
        self.field(f, "threshold", "Threshold", number(0, 255))
        for key, hi, lo in [
            ("brightness", 4, 0),
            ("contrast", 4, 0),
            ("gamma", 5, 0.1),
            ("sharpness", 5, 0),
            ("density", 1, 0),
        ]:
            self.field(f, key, key.capitalize(), number(lo, hi, 0.05))
        self.field(f, "grunge", "Ink loss", number(0, 100))
        self.field(f, "texture_seed", "Texture seed", W.QLineEdit())
        self.field(f, "invert", "Invert luminance", W.QCheckBox())
        self.button(f, "Reprocess", self.edited)
        self.button(f, "Export PNG…", self.export_dialog, True)

    def capabilities(self):
        model = self.fields["model"].currentText()
        if "scheduler" not in self.fields:
            return
        cap = CATALOG[model]
        self.model_note.setText(cap.note)
        self.left.setRowVisible(self.model_note, bool(cap.note))
        for k, enabled in [
            ("negative_prompt", cap.negative),
            ("guidance", cap.guidance),
            ("scheduler", len(cap.schedulers) > 1),
        ]:
            self.fields[k].setEnabled(enabled)
        self.fields["negative_prompt"].setToolTip(
            ""
            if cap.negative
            else "This distilled model has no effective negative prompting."
        )
        if not cap.negative:
            self.fields["negative_prompt"].setPlainText("")
            self.fields["guidance"].setValue(1)
            if not self.applying:
                self.fields["steps"].setValue(4)
            self.fields["scheduler"].setCurrentText("default")
        elif not self.applying and self.fields["guidance"].value() == 1:
            self.fields["guidance"].setValue(6)
            self.fields["steps"].setValue(28)

    def resolution(self):
        for key in ("gen_width", "gen_height"):
            self.left.setRowVisible(
                self.fields[key], self.strategy.currentText() == "Manual"
            )

    def value(self, k):
        w = self.fields[k]
        if isinstance(w, ColorInput):
            return w.color
        if isinstance(w, W.QComboBox):
            return int(w.currentText()) if k == "matrix" else w.currentText()
        if isinstance(w, W.QPlainTextEdit):
            return w.toPlainText()
        if isinstance(w, W.QLineEdit):
            return w.text()
        if isinstance(w, W.QCheckBox):
            return w.isChecked()
        return w.value()

    def processing(self):
        return Processing.model_validate(
            {k: self.value(k) for k in Processing.model_fields if k in self.fields}
        )

    def current(self):
        values = {k: self.value(k) for k in Preset.model_fields if k in self.fields}
        if self.strategy.currentText() == "Auto":
            values.update(gen_width=None, gen_height=None)
        return Preset.model_validate(
            dict(**values, name=self.name, postprocess=self.processing())
        )

    def apply(self, p):
        self.applying = True
        self.name = p.name
        data = {**p.model_dump(), **p.postprocess.model_dump()}
        self.strategy.setCurrentText("Manual" if p.gen_width else "Auto")
        for k, w in self.fields.items():
            v = data[k]
            if isinstance(w, ColorInput):
                w.edit.setText(v)
            elif isinstance(w, W.QComboBox):
                w.setCurrentText(str(v))
            elif isinstance(w, W.QPlainTextEdit):
                w.setPlainText(v)
            elif isinstance(w, W.QLineEdit):
                w.setText(str(v))
            elif isinstance(w, W.QCheckBox):
                w.setChecked(v)
            else:
                w.setValue(v if v is not None else 1024)
        self.capabilities()
        self.resolution()
        self.last = self.state()
        self.applying = False
        self.edited()

    def state(self):
        return dict(
            width=self.value("width"),
            height=self.value("height"),
            postprocess=self.processing().model_dump(),
        )

    def swap(self):
        a, b = self.fields["foreground"], self.fields["background"]
        old = a.color
        self.applying = True
        a.edit.setText(b.color)
        b.edit.setText(old)
        self.applying = False
        self.edited()

    def edited(self, *args):
        if not self.applying:
            self.serial += 1
            self.timer.start()

    def render(self):
        if self.preview_busy or self.closing:
            return
        try:
            state = self.state()
            if state != self.last:
                self.history = (self.history + [self.last])[-80:]
                self.future.clear()
                self.last = state
            if self.source is None:
                return
            p = self.processing()
            size = (state["width"], state["height"])
            original, serial = self.source, self.serial
            self.preview_busy = True

            def finish(result):
                self.preview_busy = False
                if serial == self.serial:
                    im, aligned, gray = result
                    self.viewer.canvas.processed = pixmap(im)
                    self.viewer.canvas.aligned = pixmap(aligned)
                    self.viewer.canvas.gray = pixmap(gray)
                    self.viewer.status.setText(
                        f"{im.width} × {im.height}    ·    {p.effect}    ·    {p.foreground} / {p.background}"
                    )
                    self.viewer.refresh()
                else:
                    self.timer.start()

            def prepare():
                aligned = fitted(original, size, p.fit)
                gray = luminance(aligned, p)
                return quantize(gray, p), aligned, gray

            self.task(prepare, finish, preview=True)
        except Exception as exc:
            self.error(exc)

    def task(self, work, done, gpu=False, preview=False):
        if self.closing:
            return
        self.active += 1
        future = (self.gpu if gpu else self.cpu).submit(work)
        future.add_done_callback(lambda f: self.bus.finished.emit((f, done, preview)))

    @C.Slot(object)
    def completed(self, payload):
        f, done, preview = payload
        self.active -= 1
        try:
            result = f.result()
            if not self.closing:
                done(result)
        except Exception as exc:
            if preview:
                self.preview_busy = False
            self.error(exc)
        if self.closing and not self.active:
            self.close()

    def guard(self, fn):
        try:
            fn()
        except Exception as exc:
            self.error(exc)

    def error(self, exc):
        message = str(exc)
        self.statusBar().showMessage(message)
        self.log.appendPlainText(message)
        self.logdock.show()
        try:
            self.store.log("desktop_error", message=message)
        except OSError as logging_error:
            self.log.appendPlainText(
                f"Unable to save the activity log: {logging_error}"
            )

    def set_entry(self, entry):
        self.source = self.store.source(entry)
        self.entry = entry
        self.viewer.canvas.original = pixmap(self.source)
        self.viewer.canvas.processed = self.viewer.canvas.aligned = (
            self.viewer.canvas.gray
        ) = None
        self.viewer.refresh()
        self.edited()

    def progress(self, state, fraction):
        if state == "idle":
            self.generate_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
        self.bar.setValue(round(fraction * 100))
        self.viewer.overlay.setVisible(
            state in {"loading_model", "generating", "postprocessing"}
        )
        self.viewer.overlay.setText(
            f"{state.replace('_', ' ').capitalize()}    {fraction:.0%}"
        )
        self.viewer.status.setText(state.replace("_", " ").capitalize())
        self.statusBar().showMessage(state)

    def confirm_runtime(self):
        info = runtime.status()
        if info["installed"]:
            return True
        if not info["managed"]:
            runtime.require_runtime()
        size = info["download_bytes"] / 1e9
        return (
            W.QMessageBox.question(
                self,
                "Enable local generation",
                f"Download and install the optional generation runtime ({size:.2f} GB)? "
                "Image editing works without it.",
                W.QMessageBox.Yes | W.QMessageBox.No,
                W.QMessageBox.No,
            )
            == W.QMessageBox.Yes
        )

    def generate(self):
        p = self.current()
        needs_runtime = not runtime.available()
        if needs_runtime and not self.confirm_runtime():
            return
        # Offer weights separately so enabling generation never authorizes a model implicitly.
        needs_model = needs_runtime and not model_info(self.store, p.model)["installed"]
        if needs_model:
            spec = CATALOG[p.model]
            size = 7 if p.model == "sdxl-base" else 13
            if (
                W.QMessageBox.question(
                    self,
                    "Install model",
                    f"Also download {spec.repo} (about {size} GB) to generate this image?\nLicense: {spec.license}",
                    W.QMessageBox.Yes | W.QMessageBox.No,
                    W.QMessageBox.No,
                )
                != W.QMessageBox.Yes
            ):
                return
        force = False
        if any(
            e["generation"].get("model") == p.model
            and e["generation"].get("seed") == p.seed
            for e in self.store.entries()
        ):
            box = W.QMessageBox(self)
            box.setText(
                "If all generation settings match a saved original, reuse it or generate again?"
            )
            reuse = box.addButton("Reuse when available", W.QMessageBox.AcceptRole)
            fresh = box.addButton("Force generation", W.QMessageBox.ActionRole)
            box.addButton(W.QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() not in (reuse, fresh):
                return
            force = box.clickedButton() == fresh
        self.cancel_event.clear()
        self.generate_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)

        def work():
            try:
                if needs_runtime:
                    runtime.install(
                        lambda f, msg: self.bus.progress.emit(msg, f), self.cancel_event
                    )
                if needs_model and not self.cancel_event.is_set():
                    install(
                        self.store,
                        p.model,
                        lambda f, msg: self.bus.progress.emit(msg, f),
                    )
                return self.studio.generate(
                    p,
                    force=force,
                    status=self.bus.progress.emit,
                    cancel=self.cancel_event,
                )
            finally:
                self.bus.progress.emit("idle", 0)

        def done(result):
            entry = next(
                e for e in self.store.entries() if e["id"] == result["original_id"]
            )
            self.set_entry(entry)
            self.log.appendPlainText(result["output_path"])

        self.task(work, done, gpu=True)

    def export_dialog(self):
        if not self.entry:
            raise ValueError("Import or generate an original first")
        path = W.QFileDialog.getSaveFileName(
            self, "Export PNG", str(self.store.exports / "panel.png"), "PNG (*.png)"
        )[0]
        if path:
            entry, p = self.entry, Preset(name=self.name, **self.state())
            self.task(
                lambda: self.studio.export(entry, p, path),
                lambda r: self.statusBar().showMessage(r["output_path"]),
            )

    def import_dialog(self):
        path = W.QFileDialog.getOpenFileName(
            self, "Import image", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )[0]
        if path:
            self.task(
                lambda: self.store.import_image(path), lambda r: self.set_entry(r[0])
            )

    def preview_prefs(self):
        return dict(
            mode=self.viewer.mode.currentText(),
            zoom=self.viewer.zoom.currentText(),
            checker=self.viewer.canvas.checker,
        )

    def remember(self, path):
        recent = self.settings.value("recent", [], type=list)
        self.settings.setValue(
            "recent", ([str(path)] + [x for x in recent if x != str(path)])[:8]
        )
        self.project = Path(path)
        self.setWindowTitle(f"{self.project.name}  ·  DTHR.EXE")

    def save(self, as_new=False):
        if not self.entry:
            raise ValueError("A project needs an original image")
        path = None if as_new else self.project
        path = (
            path
            or W.QFileDialog.getSaveFileName(
                self,
                "Save project",
                str(self.store.projects / "panel.ditherproj"),
                "DTHR project (*.ditherproj)",
            )[0]
        )
        if path:
            self.studio.save_project(
                path, self.entry, self.current(), self.preview_prefs()
            )
            self.remember(path)

    def open_project(self, path=None):
        path = (
            path
            or W.QFileDialog.getOpenFileName(
                self,
                "Open project",
                str(self.store.projects),
                "DTHR project (*.ditherproj)",
            )[0]
        )
        if path:
            p, entry, prefs = self.studio.open_project(path)
            self.apply(p)
            self.set_entry(entry)
            self.viewer.mode.setCurrentText(prefs.get("mode", "Processed"))
            self.viewer.zoom.setCurrentText(prefs.get("zoom", "Fit"))
            self.checker.setChecked(bool(prefs.get("checker")))
            self.history.clear()
            self.future.clear()
            self.remember(path)

    def new(self):
        self.entry = self.project = self.source = None
        self.viewer.canvas.original = self.viewer.canvas.aligned = (
            self.viewer.canvas.processed
        ) = None
        self.apply(self.store.preset())
        self.history.clear()
        self.future.clear()
        self.viewer.refresh()
        self.viewer.status.setText("No image loaded")
        self.setWindowTitle("DTHR.EXE")

    def undo(self, redo=False):
        if self.timer.isActive():
            self.render()
        src, dst = (self.future, self.history) if redo else (self.history, self.future)
        if src:
            dst.append(self.state())
            self.apply(
                Preset.model_validate({**self.current().model_dump(), **src.pop()})
            )

    def preset_dialog(self, save=False):
        if save:
            name, ok = W.QInputDialog.getText(
                self, "Save preset", "Name", text=self.name
            )
            if ok:
                p = Preset.model_validate({**self.current().model_dump(), "name": name})
                self.store.save_preset(p)
                self.name = name
        else:
            name, ok = W.QInputDialog.getItem(
                self, "Load preset", "Preset", self.store.preset_names(), editable=False
            )
            if ok:
                self.apply(self.store.preset(name))

    def defaults(self):
        if (
            W.QMessageBox.question(
                self,
                "Restore Vertical Panel",
                "Replace the saved Vertical Panel preset with its original defaults?",
            )
            == W.QMessageBox.Yes
        ):
            self.store.save_preset(Preset())
            self.apply(Preset())

    def menus(self):
        def item(menu, label, fn, key=None):
            a = menu.addAction(label)
            a.triggered.connect(lambda: self.guard(fn))
            if key:
                a.setShortcut(key)
            return a

        file = self.menuBar().addMenu("&File")
        for label, fn, key in [
            ("New project", self.new, "Ctrl+N"),
            ("Open project…", self.open_project, "Ctrl+O"),
            ("Import image…", self.import_dialog, "Ctrl+I"),
            ("Save project", self.save, "Ctrl+S"),
            ("Save project as…", lambda: self.save(True), "Ctrl+Shift+S"),
            ("Export PNG…", self.export_dialog, "Ctrl+E"),
            ("Export original…", self.export_original, None),
        ]:
            item(file, label, fn, key)
        recent = file.addMenu("Recent projects")

        def refresh_recent():
            recent.clear()
            for path in self.settings.value("recent", [], type=list):
                item(recent, Path(path).name, lambda p=path: self.open_project(p))

        recent.aboutToShow.connect(refresh_recent)
        item(file, "Exit", self.close)
        edit = self.menuBar().addMenu("&Edit")
        item(edit, "Undo", self.undo, "Ctrl+Z")
        item(edit, "Redo", lambda: self.undo(True), "Ctrl+Shift+Z")
        item(
            edit,
            "Reset post-processing",
            lambda: self.apply(
                Preset.model_validate(
                    {**self.current().model_dump(), "postprocess": {}}
                )
            ),
        )
        item(edit, "Restore preset defaults", self.defaults)
        presets = self.menuBar().addMenu("&Presets")
        item(presets, "Load preset…", self.preset_dialog)
        item(presets, "Save preset…", lambda: self.preset_dialog(True))
        item(presets, "Manage presets…", self.manage_presets)
        tools = self.menuBar().addMenu("&Tools")
        item(tools, "Model Manager…", self.models)
        item(tools, "Cache Manager…", self.cache)
        item(tools, "Agent API settings…", self.api_settings)
        item(tools, "Storage location…", self.storage_location)
        tools.addAction(self.logdock.toggleViewAction())
        self.checker = tools.addAction("Checkerboard for alpha")
        self.checker.setCheckable(True)
        self.checker.toggled.connect(self.toggle_checker)
        helpmenu = self.menuBar().addMenu("&Help")
        item(
            helpmenu,
            "Documentation",
            lambda: G.QDesktopServices.openUrl(
                C.QUrl.fromLocalFile(
                    str(Path(__file__).resolve().parent.parent / "README.md")
                )
            ),
        )
        item(
            helpmenu,
            "Diagnostics…",
            lambda: self.task(
                lambda: dict(
                    storage=str(self.store.root),
                    runtime=versions(),
                    hardware=hardware(),
                ),
                lambda r: W.QMessageBox.information(
                    self, "Diagnostics", json.dumps(r, indent=2)
                ),
            ),
        )
        item(
            helpmenu,
            "About",
            lambda: W.QMessageBox.about(
                self,
                "DTHR.EXE",
                f"DTHR.EXE   /   {__version__}\nLocal image generation and two-color print processing.",
            ),
        )

    def export_original(self):
        if not self.entry:
            raise ValueError("No original selected")
        path = W.QFileDialog.getSaveFileName(
            self,
            "Save original",
            str(self.store.exports / "original.png"),
            "PNG (*.png)",
        )[0]
        if path:
            self.store.source(self.entry)
            atomic(
                self.store.writable_output(path),
                self.store.entry_path(self.entry).read_bytes(),
            )

    def toggle_checker(self, enabled):
        self.viewer.canvas.checker = enabled
        self.viewer.refresh()

    def manage_presets(self):
        name, ok = W.QInputDialog.getItem(
            self,
            "Manage presets",
            "Remove saved preset",
            self.store.preset_names(),
            editable=False,
        )
        if (
            ok
            and W.QMessageBox.question(
                self, "Remove preset", f"Remove the saved {name} preset?"
            )
            == W.QMessageBox.Yes
        ):
            (self.store.presets / (name + ".json")).unlink(missing_ok=True)

    def storage_location(self):
        path = W.QFileDialog.getExistingDirectory(
            self, "Select data folder for next launch", str(self.store.root)
        )
        if path:
            remember_root(path)
            W.QMessageBox.information(
                self,
                "Storage location",
                "The new location takes effect on the next launch without a command-line or environment override. Existing files stay in their current folder.",
            )

    def api_settings(self):
        dialog = W.QDialog(self)
        dialog.setWindowTitle("Agent API")
        form = W.QFormLayout(dialog)
        form.addRow("Default address", W.QLabel("http://127.0.0.1:7819"))
        form.addRow("Start server", W.QLabel("dthr-cli.exe serve"))
        token = W.QLineEdit(self.store.token())
        token.setEchoMode(W.QLineEdit.Password)
        token.setReadOnly(True)
        form.addRow("Bearer token", token)
        self.button(
            form, "Copy token", lambda: W.QApplication.clipboard().setText(token.text())
        )
        dialog.exec()

    def models(self):
        dialog = W.QDialog(self)
        dialog.setWindowTitle("Model Manager")
        layout = W.QFormLayout(dialog)
        model = combo(CATALOG)
        model.setCurrentText(self.value("model"))
        layout.addRow("Model", model)
        info = W.QPlainTextEdit()
        info.setReadOnly(True)
        layout.addRow(info)

        def refresh():
            data = model_info(self.store, model.currentText())
            data["generation_runtime"] = runtime.status()
            runtime_button.setEnabled(not data["generation_runtime"]["installed"])
            info.setPlainText(
                json.dumps({**data, "loaded": self.studio.backend.active}, indent=2)
            )

        def run(action):
            name = model.currentText()
            if (
                action in ("Generation runtime", "Install", "Load")
                and not self.confirm_runtime()
            ):
                return
            if action in ("Install", "Remove"):
                spec = CATALOG[name]
                question = (
                    (
                        f"Download {spec.repo} into {self.store.models}?\nLicense: {spec.license}\n"
                        + (
                            "Approximately 7 GB."
                            if name == "sdxl-base"
                            else "Approximately 13 GB."
                        )
                    )
                    if action == "Install"
                    else f"Remove the installed {name} weights?"
                )
                if (
                    W.QMessageBox.question(dialog, action, question)
                    != W.QMessageBox.Yes
                ):
                    return
            device = self.value("device")

            def work():
                if action in ("Generation runtime", "Install", "Load"):
                    runtime.install(lambda f, msg: self.bus.progress.emit(msg, f))
                    if action == "Generation runtime":
                        return
                if action == "Install":
                    return install(
                        self.store,
                        name,
                        lambda frac, msg: self.bus.progress.emit(msg, frac),
                    )
                with (
                    self.store.lock("gpu")
                    if action != "Remove"
                    else self.store.lock("unload")
                ):
                    if action == "Load":
                        self.studio.backend.load(name, device)
                    else:
                        self.studio.backend.unload()
                if action == "Remove":
                    uninstall(self.store, name)

            self.task(work, lambda _: refresh(), gpu=True)

        runtime_button = self.button(
            layout, "Install generation support…", lambda: run("Generation runtime")
        )
        for label in ("Install", "Load", "Unload", "Remove"):
            self.button(layout, label, lambda a=label: run(a))
        self.button(
            layout,
            "Use selected model",
            lambda: self.fields["model"].setCurrentText(model.currentText()),
        )
        model.currentTextChanged.connect(refresh)
        refresh()
        dialog.resize(450, 500)
        dialog.exec()

    def cache(self):
        dialog = W.QDialog(self)
        dialog.setWindowTitle("Original cache")
        layout = W.QVBoxLayout(dialog)
        location = W.QPushButton(f"Cache location…   {self.store.originals}")

        def choose_cache():
            folder = W.QFileDialog.getExistingDirectory(
                dialog,
                "Choose parent of the originals folder",
                str(self.store.originals.parent),
            )
            if folder:
                self.store.set_cache_parent(folder)
                W.QMessageBox.information(
                    dialog,
                    "Cache location",
                    "Restart to use the selected cache. Existing originals remain at their current location.",
                )

        location.clicked.connect(lambda: self.guard(choose_cache))
        layout.addWidget(location)
        summary = W.QLabel()
        layout.addWidget(summary)
        items = W.QListWidget()
        items.setIconSize(C.QSize(48, 64))
        layout.addWidget(items)

        def refresh():
            items.clear()
            total = 0
            for e in self.store.entries():
                path = self.store.entry_path(e)
                total += path.stat().st_size if path.exists() else 0
                meta = e["generation"]
                label = meta.get("prompt") or meta.get("name", "Original")
                row = W.QListWidgetItem(
                    f"{label}\n{e['size'][0]} × {e['size'][1]}    {e['created'][:19]}"
                )
                reader = G.QImageReader(str(path))
                size = reader.size()
                if size.isValid():
                    reader.setScaledSize(size.scaled(48, 64, C.Qt.KeepAspectRatio))
                    row.setIcon(G.QIcon(G.QPixmap.fromImage(reader.read())))
                row.setData(C.Qt.UserRole, e)
                items.addItem(row)
            summary.setText(
                f"{items.count()} originals   ·   {total / 1024**2:.1f} MiB"
            )

        def selected():
            if not items.currentItem():
                raise ValueError("Select an original")
            return items.currentItem().data(C.Qt.UserRole)

        def use():
            self.set_entry(selected())
            dialog.accept()

        def remove(unused=False):
            entries = self.store.entries() if unused else [selected()]
            if unused:
                protected = {self.entry["id"]} if self.entry else set()
                for p in self.store.projects.glob("*.ditherproj"):
                    protected.add(read_json(p)["entry"]["id"])
                entries = [e for e in entries if e["id"] not in protected]
            if (
                entries
                and W.QMessageBox.question(
                    dialog,
                    "Delete originals",
                    f"Permanently delete {len(entries)} cached originals? Portable project copies are preserved.",
                )
                == W.QMessageBox.Yes
            ):
                for e in entries:
                    self.store.delete(e)
                    if self.entry and self.entry["id"] == e["id"]:
                        self.new()
                refresh()

        buttons = W.QHBoxLayout()
        layout.addLayout(buttons)
        for label, fn in [
            ("Reprocess", use),
            (
                "Open original",
                lambda: G.QDesktopServices.openUrl(
                    C.QUrl.fromLocalFile(str(self.store.entry_path(selected())))
                ),
            ),
            ("Delete", remove),
            ("Clear unused…", lambda: remove(True)),
        ]:
            b = W.QPushButton(label)
            b.clicked.connect(lambda checked=False, f=fn: self.guard(f))
            buttons.addWidget(b)
        refresh()
        dialog.resize(590, 480)
        dialog.exec()

    def closeEvent(self, event):
        self.closing = True
        self.timer.stop()
        self.cancel_event.set()
        if self.active:
            event.ignore()
            self.statusBar().showMessage("Waiting for background work to finish…")
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("split", self.split.saveState())
        self.settings.setValue("model", self.value("model"))
        self.settings.sync()
        self.cpu.shutdown(wait=True)
        self.gpu.shutdown(wait=True)
        self.studio.backend.unload()
        event.accept()


def launch(studio=None, project=None):
    app = W.QApplication.instance() or W.QApplication([])
    app.setApplicationName("DTHR.EXE")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    studio = studio or Studio()
    if not (studio.store.root / "desktop.ini").exists():
        box = W.QMessageBox()
        box.setWindowTitle("DTHR.EXE · First run")
        box.setText(f"Store models, originals and exports here?\n{studio.store.root}")
        box.addButton("Use this folder", W.QMessageBox.AcceptRole)
        choose = box.addButton("Choose folder…", W.QMessageBox.ActionRole)
        box.exec()
        if box.clickedButton() == choose:
            path = W.QFileDialog.getExistingDirectory(None, "DTHR.EXE storage")
            if not path:
                return 0
            remember_root(path)
            studio = Studio(Store(path))
    window = Window(studio)
    window.show()
    if project:
        window.guard(lambda: window.open_project(project))
    return app.exec()
