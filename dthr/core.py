import io
import math
import shutil
import tempfile
import threading
import uuid
from datetime import date
from pathlib import Path
from filelock import Timeout
from PIL import Image, ImageColor
from .effects import process
from .models import CATALOG, DiffusersBackend, hardware, versions
from .schema import Generation, Preset
from .storage import (
    Fault,
    Store,
    atomic,
    digest,
    encoded,
    png,
    publish_export,
    read_json,
    safe_path,
)


def dimensions(p, info):
    if p.gen_width:
        return p.gen_width, p.gen_height
    ratio = max(0.4, min(2.5, p.width / p.height))
    area = 1024**2 if info.get("vram_total", 32 << 30) >= 12 << 30 else 768**2
    return tuple(round(math.sqrt(area * r) / 64) * 64 for r in (ratio, 1 / ratio))


def merged_preset(store, name, overrides):
    base = store.preset(name).model_dump()
    overrides = dict(overrides)
    if "postprocess" in overrides:
        post = overrides.pop("postprocess")
        if not isinstance(post, dict):
            raise Fault("invalid_request", "postprocess must be a JSON object")
        base["postprocess"].update(post)
    return Preset.model_validate({**base, **overrides})


class Studio:
    def __init__(self, store=None, backend=None, probe=hardware):
        self.store = store or Store()
        self.backend = backend or DiffusersBackend(self.store)
        self.probe = probe

    def export(self, entry, p, path, cache_hit=True, *, published_path=None):
        path = self.store.writable_output(path)
        if path.suffix.lower() != ".png":
            raise Fault("invalid_output", "Choose a .png output file")
        image = process(self.store.source(entry), (p.width, p.height), p.postprocess)
        meta = entry["generation"]
        result = dict(
            original_id=entry["id"],
            original_path=str(self.store.entry_path(entry)),
            original_sha256=entry["sha256"],
            output_path=str(published_path or path),
            model=meta.get("model"),
            seed=meta.get("seed"),
            width=p.width,
            height=p.height,
            cache_hit=cache_hit,
            generation=meta,
            postprocess=p.postprocess.model_dump(),
            colors=[p.postprocess.foreground, p.postprocess.background],
            preset=p.name,
        )
        data = png(image, result)
        with Image.open(io.BytesIO(data)) as check:
            used = check.convert("RGB").getcolors(2)
            allowed = {ImageColor.getrgb(c) for c in result["colors"]}
            if (
                check.size != (p.width, p.height)
                or "transparency" in check.info
                or not used
                or any(c not in allowed for _, c in used)
            ):
                raise Fault(
                    "invalid_export", "PNG failed dimension or palette validation"
                )
        result.update(output_sha256=digest(data), validated=True)
        with self.store.lock(str(path)):
            publish_export(path, data, result)
        return result

    def process_file(self, source, p, path):
        if Path(source).resolve() in (
            Path(path).resolve(),
            Path(path).resolve().with_suffix(".json"),
        ):
            raise Fault(
                "protected_path", "Choose a destination different from the input file"
            )
        entry, hit = self.store.import_image(source)
        return self.export(entry, p, path, hit)

    def original(self, p, seed, force, status, cancel):
        info = self.probe()
        w, h = dimensions(p, info)
        params = {k: getattr(p, k) for k in Generation.model_fields if k != "batch"}
        params.update(
            seed=seed,
            gen_width=w,
            gen_height=h,
            runtime=versions(),
            revision=CATALOG[p.model].revision,
            model_id=CATALOG[p.model].repo,
            gpu=info.get("gpu") if p.device != "cpu" else None,
            precision="float32"
            if p.device == "cpu"
            else ("float16" if p.model == "sdxl-base" else "bfloat16"),
            cuda_version=info.get("cuda_version") if p.device != "cpu" else None,
            engine_version=1,
        )
        key = digest(encoded(params))
        lock = self.store.lock("gpu")
        while True:
            if cancel.is_set():
                raise Fault("cancelled", "Generation cancelled")
            try:
                lock.acquire(timeout=0.2)
                break
            except Timeout:
                continue
        try:
            if not force and (entry := self.store.matching(key)):
                return entry, True
            status("loading_model", 0)
            self.backend.load(p.model, p.device)
            if cancel.is_set():
                raise Fault("cancelled", "Generation cancelled")
            status("generating", 0)
            image = self.backend.generate(params, status, cancel)
            entry = self.store.save_original(image, key, params)
            return entry, False
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower():
                self.backend.unload()
                raise Fault(
                    "out_of_memory",
                    "GPU memory exhausted. Choose offload or a smaller internal resolution.",
                ) from exc
            raise
        finally:
            lock.release()

    def generate(
        self, p, path=None, force=False, status=lambda *args: None, cancel=None
    ):
        cancel = cancel or threading.Event()
        path = self.store.writable_output(
            path or self.store.exports / (uuid.uuid4().hex + ".png")
        )
        if path.suffix.lower() != ".png":
            raise Fault("invalid_output", "Choose a .png output file")
        results = []
        for i in range(p.batch):
            entry, hit = self.original(p, p.seed + i, force, status, cancel)
            if cancel.is_set():
                raise Fault(
                    "cancelled",
                    "Generation cancelled. Completed originals are available in the cache.",
                )
            status("postprocessing", 0)
            target = (
                path if i == 0 else path.with_name(f"{path.stem}-{i + 1}{path.suffix}")
            )
            results.append(self.export(entry, p, target, hit))
        return {**results[0], "items": results} if p.batch > 1 else results[0]

    def pair(self, issue, theme, left="", right="", seed=123456, directory=None):
        issue = date.fromisoformat(issue).isoformat()
        target = self.store.writable_output(
            Path(directory or self.store.exports / "panels") / issue
        )
        preset = self.store.preset()
        request = dict(
            issue=issue,
            theme=theme,
            left=left,
            right=right,
            seed=seed,
            preset=preset.model_dump(),
        )
        fingerprint = digest(encoded(request))
        with self.store.lock(str(target), timeout=3600):
            if target.exists():
                prior = read_json(target / "manifest.json")
                if prior["request_key"] != fingerprint:
                    raise Fault(
                        "pair_exists",
                        "This date has a different completed pair. Choose another output directory.",
                    )
                for item in prior["panels"].values():
                    if (
                        digest(Path(item["output_path"]).read_bytes())
                        != item["output_sha256"]
                    ):
                        raise Fault(
                            "corrupt_export", "Existing pair failed checksum validation"
                        )
                return prior
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(dir=target.parent))
            try:
                panels = {}
                for side, extra in [("left", left), ("right", right)]:
                    derived = int(digest(encoded([issue, seed, side]))[:15], 16)
                    p = Preset.model_validate(
                        {
                            **preset.model_dump(),
                            "seed": derived,
                            "batch": 1,
                            "prompt": f"{preset.prompt}. {theme}. {side} panel. {extra}".strip(),
                        }
                    )
                    entry, hit = self.original(
                        p, derived, False, lambda *a: None, threading.Event()
                    )
                    item = self.export(
                        entry,
                        p,
                        stage / (side + ".png"),
                        hit,
                        published_path=target / (side + ".png"),
                    )
                    panels[side] = item
                result = dict(
                    status="completed",
                    issue=issue,
                    request_key=fingerprint,
                    panels=panels,
                )
                atomic(stage / "manifest.json", encoded(result))
                stage.rename(target)
                return result
            finally:
                if stage.exists():
                    shutil.rmtree(stage)

    def save_project(self, path, entry, p, preview):
        path = self.store.writable_output(path)
        if path.suffix.lower() != ".ditherproj":
            raise Fault("invalid_project", "Use the .ditherproj extension")
        self.store.source(entry)
        side = path.parent / (entry["sha256"] + ".png")
        if not side.exists() or digest(side.read_bytes()) != entry["sha256"]:
            atomic(side, self.store.entry_path(entry).read_bytes())
        atomic(
            path,
            encoded(
                dict(
                    schema_version=1,
                    original=side.name,
                    entry=entry,
                    settings=p.model_dump(),
                    preview=preview,
                )
            ),
        )

    def open_project(self, path):
        raw = read_json(path)
        if raw.get("schema_version") != 1:
            raise Fault(
                "project_version",
                "Unsupported project schema. Open with a compatible version of DTHR.EXE.",
            )
        p = Preset.model_validate(raw["settings"])
        old = raw["entry"]
        source = safe_path(Path(path).resolve().parent, raw["original"])
        with self.store.lock(old["key"]):
            for entry in sorted(
                self.store.entries(), key=lambda e: e["id"] != old["id"]
            ):
                if entry["key"] == old["key"] and entry["sha256"] == old["sha256"]:
                    try:
                        self.store.source(entry)
                    except Fault:
                        continue
                    return p, entry, raw.get("preview", {})
            data = source.read_bytes()
            if digest(data) != old["sha256"]:
                raise Fault(
                    "corrupt_project",
                    "The project's companion original failed checksum verification",
                )
            with Image.open(io.BytesIO(data), formats=["PNG"]) as im:
                entry = self.store.save_original(
                    im, old["key"], old["generation"], png_data=data
                )
        return p, entry, raw.get("preview", {})
