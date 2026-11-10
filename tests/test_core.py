from pathlib import Path
import pytest
from PIL import Image
from dthr.core import Studio, merged_preset
from dthr.schema import Preset, Processing
from dthr.storage import Store, Fault, digest, read_json, safe_path


class RecordingBackend:
    active = None

    def __init__(self):
        self.calls = []

    def load(self, *args):
        pass

    def unload(self):
        pass

    def generate(self, params, status, cancel):
        self.calls.append(params.copy())
        image = (
            Image.linear_gradient("L")
            .resize((params["gen_width"], params["gen_height"]))
            .convert("RGB")
        )
        return image


@pytest.fixture
def studio(tmp_path):
    return Studio(
        Store(tmp_path / "data"),
        RecordingBackend(),
        lambda: dict(gpu="test GPU", vram_total=32 << 30),
    )


def test_cache_and_project(studio, tmp_path):
    target = studio.store.exports / "panel.png"
    p = Preset()
    a = studio.generate(p, target)
    orig = Path(a["original_path"]).read_bytes()
    changed = p.model_copy(
        update={"postprocess": Processing(effect="grunge", foreground="#9023ab")}
    )
    b = studio.generate(changed, target)
    assert (
        b["cache_hit"]
        and b["original_id"] == a["original_id"]
        and len(studio.backend.calls) == 1
    )
    entry = studio.store.entries()[0]
    file = tmp_path / "project" / "Ornament.ditherproj"
    studio.save_project(file, entry, changed, {"mode": "Split"})
    del studio
    reopened = Studio(Store(tmp_path / "fresh"), RecordingBackend())
    saved, restored, preview = reopened.open_project(file)
    result = reopened.export(restored, saved, reopened.store.exports / "restored.png")
    assert (
        preview == {"mode": "Split"} and saved == changed and not reopened.backend.calls
    )
    with Image.open(result["output_path"]) as x, Image.open(target) as y:
        assert x.tobytes() == y.tobytes()
    assert Path(a["original_path"]).read_bytes() == orig


def test_generation_keys_and_force(studio):
    p = Preset()
    first = studio.generate(p)
    for key, value in [
        ("steps", 30),
        ("prompt", "woodcut"),
        ("seed", 8),
        ("scheduler", "euler"),
        ("negative_prompt", "flowers"),
        ("guidance", 4.0),
    ]:
        assert not studio.generate(p.model_copy(update={key: value}))["cache_hit"]
    forced = studio.generate(p, force=True)
    assert first["original_id"] != forced["original_id"]
    assert len(studio.store.entries()) == 8


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_cache_integrity(studio, damage):
    r = studio.generate(Preset())
    path = Path(r["original_path"])
    path.unlink() if damage == "missing" else path.write_bytes(b"broken")
    with pytest.raises(Fault, match="damaged"):
        studio.generate(Preset())


def test_import_protection(studio, tmp_path):
    source = tmp_path / "blå bild.webp"
    Image.new("RGBA", (25, 19), (55, 90, 200, 55)).save(source)
    e, _ = studio.store.import_image(source)
    assert studio.store.import_image(source)[1]
    for path in (source, studio.store.entry_path(e)):
        with pytest.raises(Fault):
            studio.process_file(source, Preset(), path)
    result = studio.process_file(source, Preset(), tmp_path / "two colors.png")
    assert read_json(tmp_path / "two colors.json")["output_sha256"] == digest(
        Path(result["output_path"]).read_bytes()
    )


@pytest.mark.parametrize(
    "path",
    [
        "../escape.png",
        "C:\\file.png",
        "xx:yy.png",
        "sub/../x.png",
        "NUL.png",
        "\\\\server\\x",
        "a/",
    ],
)
def test_path_rejection(tmp_path, path):
    with pytest.raises(Fault):
        safe_path(tmp_path, path)


def test_pair_failure_retry(studio):
    real = studio.backend.generate

    def flaky(*args):
        if len(studio.backend.calls) == 1:
            raise ValueError("injected second panel failure")
        return real(*args)

    studio.backend.generate = flaky
    args = ("2026-11-07", "garden")
    with pytest.raises(ValueError):
        studio.pair(*args)
    target = studio.store.exports / "llmbaz" / args[0]
    assert not target.exists() and len(studio.store.entries()) == 1
    studio.backend.generate = real
    manifest = studio.pair(*args)
    left, right = manifest["panels"].values()
    assert left["cache_hit"] and left["seed"] != right["seed"] and right["validated"]
    assert left["colors"] == right["colors"] and len(studio.backend.calls) == 2
    assert studio.pair(*args) == manifest


def test_preset_persistence(studio):
    p = merged_preset(studio.store, "LLMBAZ", {"postprocess": {"strength": 91}})
    studio.store.save_preset(p)
    assert Store(studio.store.root).preset() == p
    studio.store.save_preset(Preset())
    assert studio.store.preset().postprocess.strength == 55


def test_reprocess_by_original_path(studio):
    r = studio.generate(Preset())
    changed = studio.process_file(
        r["original_path"],
        Preset(postprocess=Processing(effect="bayer")),
        studio.store.exports / "bayer.png",
    )
    assert changed["original_id"] == r["original_id"] and changed["cache_hit"]
    assert len(studio.store.entries()) == len(studio.backend.calls) == 1


def test_project_recovers_corrupt_cached_original(studio, tmp_path):
    r = studio.generate(Preset())
    p = tmp_path / "Saved.ditherproj"
    studio.save_project(p, studio.store.entries()[0], Preset(), {})
    Path(r["original_path"]).write_bytes(b"damage")
    _, restored, _ = studio.open_project(p)
    assert restored["sha256"] == r["original_sha256"]
    assert studio.open_project(p)[1] == restored


def test_failed_publication_keeps_previous_manifest(studio, monkeypatch):
    from dthr import storage

    target = studio.store.exports / "panel.png"
    studio.generate(Preset(), target)
    previous = (target.read_bytes(), target.with_suffix(".json").read_bytes())
    real = storage.os.replace

    def fail(src, dst):
        if Path(src).name == "image" and dst == target:
            raise PermissionError("test a locked PNG")
        return real(src, dst)

    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(PermissionError):
        studio.export(
            studio.store.entries()[0],
            Preset(postprocess=Processing(foreground="#ab1122")),
            target,
        )
    assert previous == (target.read_bytes(), target.with_suffix(".json").read_bytes())


def test_separate_cache_volume(studio, tmp_path):
    old = studio.generate(Preset())
    studio.store.set_cache_parent(tmp_path / "cache volume")
    new = Store(studio.store.root)
    assert new.originals == tmp_path / "cache volume/originals" and not new.entries()
    entry, hit = new.import_image(old["original_path"])
    assert (
        not hit
        and new.entry_path(entry).is_file()
        and Path(old["original_path"]).is_file()
    )
    with pytest.raises(Fault):
        new.writable_output(new.entry_path(entry))
