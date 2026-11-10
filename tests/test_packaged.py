"""Opt-in release acceptance on Windows with installed SDXL and a CUDA device."""

import ctypes
import hashlib
import json
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import psutil
import pytest
from PIL import Image
from dthr.core import Studio
from dthr.storage import Store

pytestmark = pytest.mark.skipif(
    os.getenv("DTHR_RELEASE_TEST") != "1",
    reason="Set DTHR_RELEASE_TEST=1 for real packaged Windows/GPU acceptance",
)
ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist/DTHR"
CLI = DIST / "dthr-cli.exe"


def test_packaged_desktop(tmp_path):
    # Exercise the actual GUI executable, the Windows platform plugin and a graceful close.
    (tmp_path / "desktop.ini").write_text("[General]\n", encoding="utf-8")
    env = dict(
        os.environ,
        DTHR_DATA=str(tmp_path),
        PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
    )
    env.pop("QT_QPA_PLATFORM", None)
    proc = subprocess.Popen([DIST / "DTHR.EXE"], env=env)
    try:
        until = time.monotonic() + 30
        found = []

        def check(hwnd, arg):
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            title = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, title, 256)
            children = {
                c.pid for c in psutil.Process(proc.pid).children(recursive=True)
            }
            if (
                title.value == "DTHR.EXE"
                and pid.value in children
                and ctypes.windll.user32.IsWindowVisible(hwnd)
            ):
                found.append(hwnd)
            return True

        visitor = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(
            check
        )
        while not found and time.monotonic() < until:
            assert proc.poll() is None
            ctypes.windll.user32.EnumWindows(visitor, 0)
            time.sleep(0.15)
        assert found, "Bundled native main window did not open"
        ctypes.windll.user32.PostMessageW(found[0], 0x0010, 0, 0)
        assert proc.wait(15) == 0
        assert "geometry" in (tmp_path / "desktop.ini").read_text()
    finally:
        if proc.poll() is None:
            for child in psutil.Process(proc.pid).children(recursive=True):
                child.terminate()
            proc.terminate()


def test_packaged_gpu_and_http():
    store = Store(ROOT / "data")
    env = dict(
        os.environ,
        DTHR_DATA=str(store.root),
        HF_HUB_OFFLINE="1",
        PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
    )

    def cli(*args):
        result = subprocess.run(
            [CLI, *args, "--json"], env=env, capture_output=True, text=True, timeout=300
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    actual = cli(
        "generate",
        "--preset",
        "Vertical Panel",
        "--force",
        "--output",
        str(store.exports / "panel-test.png"),
    )
    assert (
        actual["cache_hit"] is False
        and actual["generation"]["gpu"] == "NVIDIA GeForce RTX 5090"
    )
    source = Path(actual["original_path"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == actual["original_sha256"]
    with Image.open(source) as image:
        assert image.size == (
            actual["generation"]["gen_width"],
            actual["generation"]["gen_height"],
        )
        assert image.convert("L").getextrema()[0] < image.convert("L").getextrema()[1]
    with Image.open(actual["output_path"]) as image:
        assert (
            image.size == (220, 900)
            and image.mode == "P"
            and "transparency" not in image.info
        )
        assert {c for n, c in image.convert("RGB").getcolors(2)} == {
            (216, 208, 192),
            (17, 17, 17),
        }
    studio = Studio(store)
    n = len(store.entries())
    e = next(e for e in store.entries() if e["id"] == actual["original_id"])
    preset = store.preset()
    project = store.projects / "Vertical Panel.ditherproj"
    studio.save_project(project, e, preset, {"mode": "Split", "zoom": "Fit"})
    reopened, entry, prefs = studio.open_project(project)
    assert reopened == preset and entry == e and prefs["mode"] == "Split"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen([CLI, "serve", "--port", str(port)], env=env)
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": "Bearer " + store.token()},
            timeout=10,
            trust_env=False,
        ) as client:
            for _ in range(200):
                assert server.poll() is None
                try:
                    if client.get("/v1/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)

            def run(endpoint, request):
                submitted = client.post(endpoint, json=request)
                submitted.raise_for_status()
                for _ in range(300):
                    job = client.get(submitted.json()["status_url"]).json()
                    if job["status"] == "completed":
                        return job
                    assert job["status"] not in {"cancelled", "failed"}, job
                    time.sleep(0.1)
                pytest.fail("Packaged agent API timed out")

            hit = run(
                "/v1/generations",
                {"preset": "Vertical Panel", "output_name": "agent-proof.png"},
            )
            assert hit["cache_hit"] and hit["original_id"] == actual["original_id"]
            changed = run(
                "/v1/process",
                {
                    "original_id": e["id"],
                    "output_name": "panel-bayer.png",
                    "postprocess": {"effect": "bayer", "strength": 85},
                },
            )
            response = client.get("/v1/exports/panel-bayer.png")
            assert (
                response.status_code == 200
                and hashlib.sha256(response.content).hexdigest()
                == changed["output_sha256"]
            )
            assert (
                changed["original_sha256"] == actual["original_sha256"]
                and len(store.entries()) == n
            )
            assert (
                hashlib.sha256(source.read_bytes()).hexdigest()
                == actual["original_sha256"]
            )
    finally:
        if server.poll() is None:
            for child in psutil.Process(server.pid).children(recursive=True):
                child.terminate()
            server.terminate()
            server.wait(10)
