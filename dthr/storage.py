import hashlib
import io
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from filelock import FileLock
from PIL import Image, ImageOps, PngImagePlugin
from .schema import Preset


class Fault(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ).encode("utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as f:
        tmp = Path(f.name)
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def png(image, metadata=None):
    stream = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    if metadata:
        info.add_itxt("DTHR.EXE", encoded(metadata).decode("utf-8"))
    image.save(
        stream,
        format="PNG",
        pnginfo=info,
        **({"bits": 1} if image.mode == "P" and metadata else {}),
    )
    return stream.getvalue()


def publish_export(path, image_bytes, manifest):
    """Stage both files before publication and restore the prior manifest on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    side = path.with_suffix(".json")
    with tempfile.TemporaryDirectory(dir=path.parent) as scratch:
        stage = Path(scratch)
        (stage / "image").write_bytes(image_bytes)
        (stage / "manifest").write_bytes(encoded(manifest))
        if side.exists():
            if not side.is_file():
                raise Fault("invalid_output", "The manifest destination is not a file")
            shutil.copy2(side, stage / "previous")
        os.replace(stage / "manifest", side)
        try:
            os.replace(stage / "image", path)
        except OSError:
            if (stage / "previous").exists():
                os.replace(stage / "previous", side)
            else:
                side.unlink(missing_ok=True)
            raise


def inside(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def safe_path(root, name):
    pieces = re.split(r"[\\/]", name)
    if any(
        not part
        or part in {".", ".."}
        or re.search(r'[<>:"|?*\x00-\x1f]', part)
        or part.endswith((" ", "."))
        or os.path.isreserved(part)
        for part in pieces
    ):
        raise Fault("invalid_path", "Use a relative path inside the configured folder")
    path = Path(root).joinpath(*pieces).resolve()
    if not inside(path, root):
        raise Fault("invalid_path", "Path escapes configured storage")
    return path


def default_root():
    override = os.environ.get("DTHR_DATA")
    if override:
        return Path(override).resolve()
    prefs = Path(os.getenv("LOCALAPPDATA", Path.home())) / "DTHR.EXE"
    config = prefs / "location.json"
    return (
        Path(read_json(config)["root"])
        if config.exists()
        else Path(os.getenv("DTHR_DEFAULT_DATA", prefs / "data"))
    )


def remember_root(path):
    prefs = Path(os.getenv("LOCALAPPDATA", Path.home())) / "DTHR.EXE/location.json"
    atomic(prefs, encoded({"root": str(Path(path).resolve())}))


class Store:
    def __init__(self, root=None):
        self.root = Path(root or default_root()).resolve()
        for folder in (
            "exports",
            "imports",
            "projects",
            "presets",
            "models",
            "locks",
        ):
            setattr(self, folder, self.root / folder)
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        config = self.root / "cache-location.json"
        parent = (
            Path(read_json(config)["parent"]).resolve()
            if config.exists()
            else self.root
        )
        self.originals = parent / "originals"
        self.originals.mkdir(parents=True, exist_ok=True)

    def set_cache_parent(self, parent):
        parent = Path(parent).resolve()
        if inside(parent, self.root) and parent != self.root:
            raise Fault(
                "invalid_path",
                "Choose the application root or a separate cache location",
            )
        (parent / "originals").mkdir(parents=True, exist_ok=True)
        atomic(self.root / "cache-location.json", encoded({"parent": str(parent)}))

    def lock(self, scope, timeout=120):
        return FileLock(
            str(self.locks / (digest(scope.casefold().encode()) + ".lock")),
            timeout=timeout,
        )

    def log(self, event, **fields):
        with self.lock("events"):
            with (self.root / "events.jsonl").open("a", encoding="utf-8") as out:
                out.write(
                    json.dumps(
                        dict(time=utc(), event=event, **fields), ensure_ascii=False
                    )
                    + "\n"
                )
        logging.getLogger("dthr").info("%s %s", event, fields)

    def token(self):
        path = self.root / "api.token"
        with self.lock("token"):
            if not path.exists():
                atomic(path, secrets.token_hex(32).encode())
            return path.read_text().strip()

    def preset(self, name="LLMBAZ"):
        path = safe_path(self.presets, name + ".json")
        if name == "LLMBAZ" and not path.exists():
            return Preset()
        return Preset.model_validate(read_json(path))

    def save_preset(self, preset):
        atomic(
            safe_path(self.presets, preset.name + ".json"), encoded(preset.model_dump())
        )

    def preset_names(self):
        return sorted({"LLMBAZ", *(p.stem for p in self.presets.glob("*.json"))})

    def entries(self):
        return [
            read_json(p)
            for p in sorted(
                self.originals.glob("[0-9a-f]*/entry.json"),
                key=lambda p: p.stat().st_mtime_ns,
                reverse=True,
            )
        ]

    def entry_path(self, entry):
        return safe_path(self.originals, entry["id"] + "/original.png")

    def source(self, entry):
        path = self.entry_path(entry)
        if not path.exists() or digest(data := path.read_bytes()) != entry["sha256"]:
            raise Fault("corrupt_original", f"Original missing or damaged: {path}")
        with Image.open(io.BytesIO(data)) as im:
            return im.copy()

    def matching(self, key):
        for entry in self.entries():
            if entry["key"] == key:
                self.source(entry)
                self.log("cache_hit", id=entry["id"])
                return entry
        return None

    def save_original(self, image, key, metadata, *, png_data=None):
        data = png(image) if png_data is None else png_data
        entry = dict(
            id=uuid.uuid4().hex,
            key=key,
            sha256=digest(data),
            created=utc(),
            size=list(image.size),
            generation=metadata,
        )
        # Stage on the same volume, outside the set of published hexadecimal IDs.
        stage = Path(tempfile.mkdtemp(prefix=".incoming-", dir=self.originals))
        try:
            (stage / "original.png").write_bytes(data)
            (stage / "entry.json").write_bytes(encoded(entry))
            stage.rename(self.originals / entry["id"])
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        self.log("original_saved", id=entry["id"])
        return entry

    def import_image(self, path):
        path = Path(path).resolve()
        if inside(path, self.originals) and path.name == "original.png":
            entry = read_json(path.with_name("entry.json"))
            if self.entry_path(entry) != path:
                raise Fault(
                    "corrupt_original", "Original metadata does not match its location"
                )
            self.source(entry)
            self.log("cache_hit", id=entry["id"])
            return entry, True
        if not path.is_file():
            raise Fault("missing_source", f"Image does not exist: {path}")
        if path.stat().st_size > 128 * 1024**2:
            raise Fault("invalid_image", "Image file exceeds 128 MiB")
        with Image.open(path, formats=["PNG", "JPEG", "WEBP"]) as image:
            if image.width * image.height > 32_000_000:
                raise Fault("invalid_image", "Maximum source size is 32 megapixels")
            source = ImageOps.exif_transpose(image).convert(
                "RGBA"
                if "A" in image.getbands() or "transparency" in image.info
                else "RGB"
            )
        key = "import-" + digest(png(source))
        with self.lock(key):
            existing = self.matching(key)
            if existing:
                return existing, True
            return self.save_original(
                source, key, {"kind": "import", "name": path.name}
            ), False

    def delete(self, entry):
        folder = self.entry_path(entry).parent
        if folder.parent != self.originals or not re.fullmatch(
            "[0-9a-f]{32}", folder.name
        ):
            raise Fault("invalid_path", "Invalid cache entry")
        with self.lock(entry["key"]):
            shutil.rmtree(folder)

    def writable_output(self, path):
        path = Path(path).resolve()
        if inside(path, self.originals) or (
            inside(path, self.root)
            and not inside(path, self.exports)
            and not inside(path, self.projects)
        ):
            raise Fault(
                "protected_path",
                "Choose an export or project folder outside managed originals, models and settings",
            )
        return path
