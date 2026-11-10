import hmac
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field, ValidationError
from .core import Studio, merged_preset
from .jobs import Jobs
from . import runtime
from .models import CATALOG, model_info
from .schema import DEFAULT_PRESET, Name, Schema
from .storage import Fault, safe_path


class GenerateBody(Schema):
    preset: Name = DEFAULT_PRESET
    output_name: str | None = Field(None, max_length=240)
    force: bool = False
    # Overrides are validated by Preset after merging with persisted values.
    model_config = dict(extra="allow")


class ProcessBody(Schema):
    original_id: str | None = Field(None, pattern="^[0-9a-f]{32}$")
    input: str | None = Field(None, max_length=240)
    preset: Name = DEFAULT_PRESET
    output_name: str = Field(max_length=240)
    model_config = dict(extra="allow")


class ImportBody(Schema):
    input: str = Field(max_length=240)


def create_app(studio=None):
    studio = studio or Studio()
    store = studio.store
    token = store.token()
    jobs = Jobs()

    @asynccontextmanager
    async def lifespan(app):
        yield
        jobs.close()
        studio.backend.unload()

    app = FastAPI(
        title="DTHR.EXE", version="1", lifespan=lifespan, docs_url=None, redoc_url=None
    )

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if not hmac.compare_digest(
            request.headers.get("authorization", "").encode(),
            ("Bearer " + token).encode(),
        ):
            return JSONResponse(
                {"code": "unauthorized", "message": "Bearer token required"},
                status_code=401,
            )
        if request.method == "POST":
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 65536:
                    return JSONResponse({"code": "too_large"}, status_code=413)
                body.extend(chunk)
            request._body = bytes(body)
        return await call_next(request)

    @app.exception_handler(Fault)
    async def fault(request, exc):
        return JSONResponse(
            dict(code=exc.code, message=str(exc)),
            status_code={"unknown_job": 404, "queue_full": 429}.get(exc.code, 400),
        )

    @app.exception_handler(ValidationError)
    async def invalid(request, exc):
        return JSONResponse(
            dict(code="invalid_request", message=str(exc)), status_code=422
        )

    @app.exception_handler(OSError)
    async def io_error(request, exc):
        return JSONResponse(
            dict(code="storage_error", message=str(exc)), status_code=400
        )

    @app.get("/v1/health")
    def health():
        return dict(
            status="ok",
            service="DTHR.EXE",
            version=1,
            generation_runtime=runtime.status(),
        )

    @app.get("/v1/models")
    def models():
        return [model_info(store, k) for k in CATALOG]

    @app.get("/v1/cache")
    def cache():
        return store.entries()

    @app.get("/v1/presets")
    def presets():
        return [store.preset(n) for n in store.preset_names()]

    def output(name):
        path = safe_path(store.exports, name)
        if path.suffix.lower() != ".png":
            raise Fault("invalid_output", "Export name must end in .png")
        return path

    @app.post("/v1/generations", status_code=202)
    def generation(body: GenerateBody):
        preset = merged_preset(store, body.preset, body.model_extra)
        path = output(body.output_name) if body.output_name else None
        return jobs.submit(
            lambda status, cancel: studio.generate(
                preset, path, body.force, status, cancel
            )
        )

    @app.get("/v1/exports/{name:path}")
    def download(name: str):
        path = output(name)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Export not found")
        return FileResponse(path, media_type="image/png", filename=path.name)

    @app.post("/v1/process", status_code=202)
    def process(body: ProcessBody):
        if set(body.model_extra) - {"width", "height", "postprocess"}:
            raise Fault(
                "invalid_request",
                "Processing accepts only dimensions and postprocess overrides",
            )
        p = merged_preset(store, body.preset, body.model_extra)
        path = output(body.output_name)
        if bool(body.original_id) == bool(body.input):
            raise Fault(
                "invalid_source", "Specify exactly one of original_id and input"
            )
        if body.original_id:
            entry = next(
                (e for e in store.entries() if e["id"] == body.original_id), None
            )
            if not entry:
                raise Fault("missing_source", "Original ID not found")
        else:
            source = safe_path(store.imports, body.input)

        def work(status, cancel):
            status("postprocessing", 0)
            return (
                studio.export(entry, p, path)
                if body.original_id
                else studio.process_file(source, p, path)
            )

        return jobs.submit(work, gpu=False)

    @app.post("/v1/imports")
    def import_image(body: ImportBody):
        entry, hit = store.import_image(safe_path(store.imports, body.input))
        return dict(original=entry, cache_hit=hit)

    @app.get("/v1/jobs/{id}")
    def job(id: str):
        return jobs.get(id)

    @app.post("/v1/jobs/{id}/cancel")
    def cancel(id: str):
        return jobs.cancel(id)

    return app
