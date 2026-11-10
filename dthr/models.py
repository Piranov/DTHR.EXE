import gc
import importlib.metadata as metadata
import os
import shutil
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Protocol
from .storage import Fault, atomic, encoded, read_json


@dataclass(frozen=True)
class Model:
    repo: str
    revision: str
    license: str
    negative: bool
    guidance: bool
    schedulers: tuple
    patterns: tuple

    @property
    def note(self):
        return (
            ""
            if self.negative
            else "Experimental Klein adapter. The distilled checkpoint uses fixed guidance and does not apply negative prompts."
        )

    def capabilities(self):
        return dict(
            supports_negative_prompt=self.negative,
            supports_guidance=self.guidance,
            supports_seed=True,
            supports_custom_resolution=True,
            schedulers=self.schedulers,
            note=self.note,
        )


TOKENIZER = ("tokenizer/*.json", "tokenizer/*.txt", "tokenizer/*.jinja")
CATALOG = {
    "sdxl-base": Model(
        "stabilityai/stable-diffusion-xl-base-1.0",
        "462165984030d82259a11f4367a4eed129e94a7b",
        "CreativeML Open RAIL++-M",
        True,
        True,
        ("default", "euler", "dpm++"),
        (
            *TOKENIZER,
            "tokenizer_2/*.txt",
            "tokenizer_2/*.json",
            "text_encoder*/config.json",
            "text_encoder*/model.fp16.safetensors",
            "vae/config.json",
            "vae/*.fp16.safetensors",
            "unet/config.json",
            "unet/*.fp16.safetensors",
        ),
    ),
    "flux2-klein-4b": Model(
        "black-forest-labs/FLUX.2-klein-4B",
        "e7b7dc27f91deacad38e78976d1f2b499d76a294",
        "Apache-2.0",
        False,
        False,
        ("default",),
        (
            *TOKENIZER,
            "text_encoder/*.json",
            "text_encoder/*.safetensors",
            "vae/config.json",
            "vae/*.safetensors",
            "transformer/config.json",
            "transformer/*.safetensors",
        ),
    ),
}


def versions():
    names = (
        "torch",
        "torchvision",
        "diffusers",
        "transformers",
        "accelerate",
        "safetensors",
        "numpy",
        "pillow",
    )
    result = {}
    for name in names:
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result[name] = "missing"
    return result


def hardware():
    try:
        import torch

        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            return dict(
                cuda=True,
                gpu=torch.cuda.get_device_name(),
                cuda_version=torch.version.cuda,
                vram_free=free,
                vram_total=total,
            )
        return dict(
            cuda=False,
            message="CUDA unavailable. Choose CPU explicitly or install a compatible NVIDIA driver.",
        )
    except (ImportError, OSError, RuntimeError) as exc:
        return dict(cuda=False, message=str(exc))


def model_info(store, name):
    spec = CATALOG[name]
    folder = store.models / name
    marker = folder / "installed.json"
    installed = marker.exists() and read_json(marker).get("revision") == spec.revision
    return dict(
        model=name,
        repo=spec.repo,
        revision=spec.revision,
        license=spec.license,
        installed=installed,
        path=str(folder),
        bytes=sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()),
        capabilities=spec.capabilities(),
        missing_dependencies=[k for k, v in versions().items() if v == "missing"],
    )


def install(store, name, progress=lambda *args: None):
    spec = CATALOG[name]
    os.environ.setdefault("HF_HOME", str(store.root / "hub"))
    from huggingface_hub import HfApi, hf_hub_download

    with store.lock("model-" + name):
        if model_info(store, name)["installed"]:
            return model_info(store, name)
        stage = store.models / (name + ".partial")
        patterns = (
            *spec.patterns,
            "model_index.json",
            "scheduler/scheduler_config.json",
            "LICENSE.md",
        )
        try:
            info = HfApi().model_info(
                spec.repo, revision=spec.revision, files_metadata=True
            )
            files = [
                f
                for f in info.siblings
                if any(fnmatchcase(f.rfilename, p) for p in patterns)
            ]
            if not files or not any(
                f.rfilename.endswith(".safetensors") for f in files
            ):
                raise ValueError("Model revision has no supported weights")
            total = sum(f.size for f in files)
            done = 0
            for f in files:
                progress(done / total, f.rfilename)
                dest = Path(
                    hf_hub_download(
                        spec.repo, f.rfilename, revision=spec.revision, local_dir=stage
                    )
                )
                if dest.stat().st_size != f.size:
                    raise ValueError("Incomplete model file: " + f.rfilename)
                if f.lfs:
                    import hashlib

                    with dest.open("rb") as handle:
                        if (
                            hashlib.file_digest(handle, "sha256").hexdigest()
                            != f.lfs.sha256
                        ):
                            raise ValueError("Model checksum mismatch: " + f.rfilename)
                done += f.size
            atomic(stage / "installed.json", encoded(dict(revision=spec.revision)))
            stage.rename(store.models / name)
            progress(1, "Installed")
        except Exception as exc:
            raise Fault(
                "download_failed", f"Model installation failed. Retry to resume: {exc}"
            ) from exc
        return model_info(store, name)


def uninstall(store, name):
    if name not in CATALOG:
        raise ValueError("Unknown model")
    with store.lock("gpu"), store.lock("model-" + name):
        folder = store.models / name
        if folder.is_dir():
            shutil.rmtree(folder)


class ImageBackend(Protocol):
    def load(self, model, device): ...
    def unload(self): ...
    def generate(self, params, status, cancel): ...
    def capabilities(self, name): ...
    def model_info(self, name): ...


class DiffusersBackend:
    def __init__(self, store):
        self.store = store
        self.pipe = None
        self.active = None

    def capabilities(self, name):
        return CATALOG[name].capabilities()

    def model_info(self, name):
        return model_info(self.store, name)

    def load(self, model, device):
        if self.active == (model, device):
            return
        self.unload()
        if not self.model_info(model)["installed"]:
            raise Fault(
                "missing_model", f"Install {model} in Tools → Model Manager first."
            )
        import torch

        if device != "cpu" and not torch.cuda.is_available():
            raise Fault("cuda_unavailable", hardware()["message"])
        from diffusers import StableDiffusionXLPipeline, Flux2KleinPipeline

        klass = (
            StableDiffusionXLPipeline if model == "sdxl-base" else Flux2KleinPipeline
        )
        dtype = (
            torch.float32
            if device == "cpu"
            else (torch.float16 if model == "sdxl-base" else torch.bfloat16)
        )
        kwargs = dict(torch_dtype=dtype, local_files_only=True, use_safetensors=True)
        if model == "sdxl-base":
            kwargs.update(variant="fp16", add_watermarker=False)
        pipe = klass.from_pretrained(str(self.store.models / model), **kwargs)
        if device == "offload":
            pipe.enable_model_cpu_offload()
        else:
            pipe.to(device)
        pipe.set_progress_bar_config(disable=True)
        self.scheduler_config = dict(pipe.scheduler.config)
        self.scheduler_class = type(pipe.scheduler)
        self.pipe, self.active = pipe, (model, device)

    def unload(self):
        self.pipe = self.active = None
        import sys

        if "torch" in sys.modules:
            gc.collect()
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def generate(self, params, status, cancel):
        import torch
        from diffusers import EulerDiscreteScheduler, DPMSolverMultistepScheduler

        def step(pipe, index, timestep, kwargs):
            if cancel.is_set():
                raise Fault("cancelled", "Generation cancelled")
            status("generating", (index + 1) / params["steps"])
            return kwargs

        scheduler = {
            "default": self.scheduler_class,
            "euler": EulerDiscreteScheduler,
            "dpm++": DPMSolverMultistepScheduler,
        }[params["scheduler"]]
        self.pipe.scheduler = scheduler.from_config(self.scheduler_config)
        params["effective_scheduler"] = type(self.pipe.scheduler).__name__
        if CATALOG[params["model"]].note:
            params["capability_note"] = CATALOG[params["model"]].note
        kwargs = dict(
            prompt=params["prompt"],
            width=params["gen_width"],
            height=params["gen_height"],
            guidance_scale=params["guidance"],
            num_inference_steps=params["steps"],
            generator=torch.Generator(device="cpu").manual_seed(params["seed"]),
            callback_on_step_end=step,
        )
        if self.capabilities(params["model"])["supports_negative_prompt"]:
            kwargs["negative_prompt"] = params["negative_prompt"]
        with torch.inference_mode():
            return self.pipe(**kwargs).images[0]
