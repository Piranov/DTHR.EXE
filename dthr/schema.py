from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Dimension = Annotated[int, Field(ge=1, le=4096)]
RGB_PATTERN = r"^#[a-fA-F0-9]{6}$"
Color = Annotated[str, StringConstraints(pattern=RGB_PATTERN)]
Name = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9 _-]{0,63}$")]
Seed = Annotated[int, Field(ge=0, le=2**63 - 1)]
PROMPT = "Surreal gothic botanical engraving, mechanical roots, antique illustration"
NEGATIVE = "text, letters, logos, modern objects"


class Schema(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, validate_default=True
    )


class Processing(Schema):
    effect: Literal["pixel", "floyd", "bayer", "threshold", "grunge"] = "pixel"
    strength: int = Field(55, ge=0, le=100)
    foreground: Color = "#d8d0c0"
    background: Color = "#111111"
    palette_size: Literal[2] = 2
    transparent: Literal[False] = False
    fit: Literal["auto", "cover", "contain"] = "auto"
    block_size: int = Field(0, ge=0, le=128)
    matrix: Literal[2, 4, 8] = 4
    threshold: int = Field(128, ge=0, le=255)
    brightness: float = Field(1, ge=0, le=4)
    contrast: float = Field(1, ge=0, le=4)
    gamma: float = Field(1, ge=0.1, le=5)
    sharpness: float = Field(1, ge=0, le=5)
    invert: bool = False
    grunge: int = Field(0, ge=0, le=100)
    density: float = Field(0.12, ge=0, le=1)
    texture_seed: Seed = 0


class Generation(Schema):
    model: Literal["sdxl-base", "flux2-klein-4b"] = "sdxl-base"
    prompt: str = Field(PROMPT, min_length=1, max_length=4096)
    negative_prompt: str = Field(NEGATIVE, max_length=4096)
    seed: Seed = 123456
    steps: int = Field(28, ge=1, le=150)
    guidance: float = Field(6.0, ge=1, le=30)
    scheduler: Literal["default", "euler", "dpm++"] = "default"
    gen_width: int | None = Field(None, ge=256, le=2048, multiple_of=16)
    gen_height: int | None = Field(None, ge=256, le=2048, multiple_of=16)
    batch: int = Field(1, ge=1, le=8)
    device: Literal["cuda", "offload", "cpu"] = "cuda"

    @model_validator(mode="after")
    def valid(self):
        if not self.prompt.strip():
            raise ValueError("Prompt is empty")
        if (self.gen_width is None) != (self.gen_height is None):
            raise ValueError("Set both generation dimensions or choose Auto")
        if self.seed + self.batch - 1 > 2**63 - 1:
            raise ValueError("Batch seeds exceed the maximum")
        if self.model == "sdxl-base" and self.guidance <= 1 and self.negative_prompt:
            raise ValueError("SDXL needs guidance above 1 for a negative prompt")
        if self.model == "flux2-klein-4b" and (
            self.negative_prompt or self.guidance != 1 or self.scheduler != "default"
        ):
            raise ValueError(
                "Distilled Klein uses guidance 1, its own scheduler and no negative prompt"
            )
        return self


class Export(Schema):
    format: Literal["png"] = "png"
    exact_dimensions: Literal[True] = True


class Preset(Generation):
    name: Name = "LLMBAZ"
    width: Dimension = 220
    height: Dimension = 900
    postprocess: Processing = Field(default_factory=Processing)
    export: Export = Field(default_factory=Export)
