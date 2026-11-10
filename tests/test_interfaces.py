import json
import subprocess
import sys
import threading
import time
from fastapi.testclient import TestClient
from PIL import Image
from dthr.api import create_app
from dthr.core import Studio
from dthr.jobs import Jobs, TERMINAL
from dthr.storage import Fault, Store
from test_core import RecordingBackend


def wait_for(fn):
    until = time.monotonic() + 8
    while time.monotonic() < until:
        job = fn()
        if job["status"] in TERMINAL:
            return job
        time.sleep(0.01)
    raise AssertionError("Job did not finish")


def test_cli(tmp_path):
    def call(*args):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "dthr",
                "--data-dir",
                str(tmp_path / "data"),
                *map(str, args),
                "--json",
            ],
            capture_output=True,
            text=True,
        )

    source = tmp_path / "gröna rötter.png"
    Image.linear_gradient("L").save(source)
    result = call("process", "--input", source, "--output", tmp_path / "dag 1.png")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["validated"]
    result = call("generate", "--steps", 0)
    assert (
        result.returncode == 2
        and json.loads(result.stderr)["code"] == "invalid_request"
    )


def test_api(tmp_path):
    studio = Studio(Store(tmp_path), RecordingBackend(), lambda: dict(cuda=False))
    with TestClient(create_app(studio)) as client:
        assert client.get("/v1/health").status_code == 401
        client.headers["Authorization"] = "Bearer " + studio.store.token()
        assert client.get("/v1/health").status_code == 200

        def job(path, body):
            r = client.post(path, json=body)
            assert r.status_code == 202, r.text
            return wait_for(lambda: client.get(r.json()["status_url"]).json())

        a = job("/v1/generations", {"output_name": "panel.png"})
        assert a["status"] == "completed" and a["seed"] == 123456
        b = job(
            "/v1/process",
            {
                "original_id": a["original_id"],
                "output_name": "color.png",
                "postprocess": {"foreground": "#aa8844"},
            },
        )
        assert (
            b["original_sha256"] == a["original_sha256"]
            and len(studio.backend.calls) == 1
        )
        assert (
            client.post(
                "/v1/generations", json={"output_name": "../bad.png"}
            ).status_code
            == 400
        )
        assert (
            client.post("/v1/imports", json={"input": "C:\\bad.png"}).status_code == 400
        )
        assert client.post("/v1/generations", json={"stpes": 10}).status_code == 422
        assert (
            client.post("/v1/generations", json={"prompt": "x" * 70000}).status_code
            == 413
        )
        assert (
            client.post("/v1/generations", json={"model": "flux2-klein-4b"}).status_code
            == 422
        )
        assert client.get("/v1/jobs/unknown").status_code == 404


def test_concurrent_jobs():
    jobs = Jobs(capacity=3)
    started = threading.Event()

    def active(status, cancel):
        status("generating", 0.2)
        started.set()
        if cancel.wait(4):
            raise Fault("cancelled", "Stopped")
        raise AssertionError("Not cancelled")

    try:
        first = jobs.submit(active)["job_id"]
        assert started.wait(2)
        second = jobs.submit(active)["job_id"]
        assert jobs.get(second)["status"] == "queued"
        assert jobs.cancel(second)["status"] == "cancelled"
        cpu = jobs.submit(lambda update, cancel: {"ok": True}, False)["job_id"]
        assert wait_for(lambda: jobs.get(cpu))["ok"]
        jobs.cancel(first)
        assert wait_for(lambda: jobs.get(first))["status"] == "cancelled"
    finally:
        jobs.close()
