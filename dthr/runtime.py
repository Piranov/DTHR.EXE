"""Opt-in installation of the release's pinned image generation dependencies."""

import hashlib
import importlib
import importlib.util
from importlib.machinery import PathFinder
import json
import shutil
import sys
import tempfile
import threading
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from filelock import FileLock, Timeout
from . import __version__
from .storage import Fault, safe_path

APP = Path(__file__).resolve().parent.parent
RELEASES = "https://github.com/Piranov/DTHR.EXE/releases/download"
MODULES = (
    "torch",
    "torchvision",
    "diffusers",
    "transformers",
    "accelerate",
    "safetensors",
)


def manifest():
    path = APP / "generation.json"
    if not path.exists():
        return None
    data = json.loads(path.read_bytes())
    if data["version"] != __version__:
        raise Fault(
            "runtime_version",
            "Reinstall the editor to restore its matching generation manifest.",
        )
    return data


def component_dir(data):
    # A rebuild with different pinned archives gets a separate, immutable directory.
    identity = hashlib.sha256(
        json.dumps(data["chunks"], sort_keys=True).encode()
    ).hexdigest()[:16]
    return APP / "generation-components" / (data["version"] + "-" + identity)


def available():
    data = manifest()
    if data:
        directory = component_dir(data)
        if (directory / ".complete").is_file() and str(directory) not in sys.path:
            sys.path.append(str(directory))
            importlib.invalidate_caches()
    # Also recognizes full source installs and upgrades from the 1.0 package.
    return all(importlib.util.find_spec(name) is not None for name in MODULES)


def status():
    data = manifest()
    return dict(
        installed=available(),
        managed=data is not None,
        download_bytes=sum(c["size"] for c in data["chunks"]) if data else None,
    )


def require_runtime():
    if not available():
        raise Fault(
            "missing_generation_runtime",
            "Install generation support from Model Manager or run dthr-cli.exe runtime install --confirm.",
        )


def check_cancel(cancel):
    if cancel.is_set():
        raise Fault(
            "cancelled",
            "Generation runtime installation cancelled. Verified downloads can be reused.",
        )


def verify(path, chunk):
    if not path.is_file() or path.stat().st_size != chunk["size"]:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == chunk["sha256"]


def install(progress=lambda fraction, message: None, cancel=None, source=None):
    cancel = cancel or threading.Event()
    if available():
        return status()
    data = manifest()
    if not data:
        raise Fault(
            "runtime_unavailable",
            "Install the Windows release or install the project's locked development dependencies.",
        )
    target = component_dir(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(target) + ".lock")
    while True:
        check_cancel(cancel)
        try:
            lock.acquire(timeout=0.3)
            break
        except Timeout:
            progress(0, "Waiting for generation runtime installer")
    try:
        if available():
            return status()
        chunks = data["chunks"]
        total = sum(c["size"] for c in chunks)
        cache = target.with_suffix(target.suffix + ".downloads")
        cache.mkdir(exist_ok=True)
        source = Path(source) if source else None
        files = []
        done = 0
        for chunk in chunks:
            check_cancel(cancel)
            name = chunk["name"]
            path = safe_path(source or cache, name)
            if not verify(path, chunk):
                if source:
                    raise Fault(
                        "invalid_runtime_archive",
                        f"Missing or incorrect release archive: {name}",
                    )
                url = f"{RELEASES}/v{__version__}/{name}"
                temp = path.with_suffix(".part")
                try:
                    progress(done / total, f"Downloading {name}")
                    with (
                        urlopen(
                            Request(
                                url, headers={"User-Agent": "DTHR.EXE/" + __version__}
                            ),
                            timeout=60,
                        ) as response,
                        temp.open("wb") as out,
                    ):
                        size = 0
                        digest = hashlib.sha256()
                        while block := response.read(1024 * 1024):
                            check_cancel(cancel)
                            size += len(block)
                            if size > chunk["size"]:
                                raise Fault(
                                    "invalid_runtime_archive",
                                    f"Unexpected size for {name}",
                                )
                            digest.update(block)
                            out.write(block)
                            progress(
                                0.85 * (done + size) / total, f"Downloading {name}"
                            )
                    if size != chunk["size"] or digest.hexdigest() != chunk["sha256"]:
                        raise Fault(
                            "invalid_runtime_archive",
                            f"Checksum mismatch for {name}. Please retry.",
                        )
                    temp.replace(path)
                finally:
                    temp.unlink(missing_ok=True)
            files.append(path)
            done += chunk["size"]
        # Keep old installs usable until every new file is ready.
        with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
            stage = Path(tmp) / "packages"
            stage.mkdir()
            for i, path in enumerate(files):
                progress(0.85 + 0.15 * i / len(files), f"Installing {path.name}")
                with zipfile.ZipFile(path) as archive:
                    for item in archive.infolist():
                        check_cancel(cancel)
                        dest = safe_path(stage, item.filename)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(item) as src, dest.open("wb") as out:
                            shutil.copyfileobj(src, out, 1024 * 1024)
            check_cancel(cancel)
            if any(
                PathFinder.find_spec(name, [str(stage)]) is None for name in MODULES
            ):
                raise Fault(
                    "invalid_runtime_archive",
                    "Generation archives are missing a required package.",
                )
            (stage / ".complete").write_text(data["version"], encoding="ascii")
            stage.rename(target)
        if not available():
            raise Fault(
                "runtime_incomplete",
                "Generation runtime is incomplete. Reinstall the editor and generation support.",
            )
        shutil.rmtree(cache, ignore_errors=True)
        progress(1, "Generation runtime ready")
        return status()
    except OSError as exc:
        raise Fault(
            "runtime_install_failed",
            f"Could not install generation support. Check free disk space, the installation folder and network connection, then retry: {exc}",
        ) from exc
    finally:
        lock.release()
