import hashlib
import json
import sys
import threading
import zipfile

import pytest
from dthr import runtime
from dthr.storage import Fault


@pytest.fixture
def component(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    name = "dthr_test_optional_package"
    monkeypatch.setattr(runtime, "APP", app)
    monkeypatch.setattr(runtime, "MODULES", (name,))
    monkeypatch.setattr(sys, "path", sys.path.copy())
    src = tmp_path / "release"
    src.mkdir()
    archive = src / "DTHR-generation-x64-01.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(name + "/__init__.py", "")
    chunk = {
        "name": archive.name,
        "size": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "unpacked": 0,
    }
    (app / "generation.json").write_text(
        json.dumps({"version": runtime.__version__, "chunks": [chunk]})
    )
    return archive


def test_offline_install_is_explicit_and_reusable(component, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Unexpected network request")

    monkeypatch.setattr(runtime, "urlopen", unexpected)
    assert runtime.status() == dict(
        installed=False, managed=True, download_bytes=component.stat().st_size
    )
    with pytest.raises(Fault) as err:
        runtime.require_runtime()
    assert err.value.code == "missing_generation_runtime"
    assert runtime.install(source=component.parent)["installed"]
    component.unlink()
    assert runtime.install()["installed"]


def test_corrupt_or_cancelled_install_keeps_editor_available(component):
    original = component.read_bytes()
    component.write_bytes(bytes(len(original)))
    with pytest.raises(Fault) as err:
        runtime.install(source=component.parent)
    assert err.value.code == "invalid_runtime_archive" and not runtime.available()
    component.write_bytes(original)
    event = threading.Event()
    event.set()
    with pytest.raises(Fault) as err:
        runtime.install(source=component.parent, cancel=event)
    assert err.value.code == "cancelled" and not runtime.available()
    assert runtime.install(source=component.parent)["installed"]
