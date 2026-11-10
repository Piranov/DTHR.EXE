# DTHR.EXE

DTHR.EXE is a local Windows image workstation for generating originals and repeatedly printing them through a two-color palette. Generation runs in Diffusers. Preview, export, import and project reopening use Pillow and NumPy independently of the model.

## Start on Windows 11 x64

Open **dist/DTHR/DTHR.EXE**. Keep the entire DTHR folder together. It contains its own Python 3.13.5, Qt, CUDA-enabled PyTorch runtime, application and console launcher. The Windows .NET Framework 4.x runs the launchers. An NVIDIA driver supporting CUDA 12.8 and the card is required for GPU inference. CPU processing and the GUI work without CUDA.

On first run, confirm the data folder or choose one with room for weights and originals. This workspace build points to the tested project data folder through `storage-location.txt`. For a portable installation, remove that file or change its path. `DTHR_DATA` overrides both the file and the desktop preference. The CLI also accepts `--data-dir PATH` **before** its command. Tools → Storage location selects the default for subsequent launches without an override.

Open Tools → Model Manager. Select **sdxl-base**, review its license, then choose Install. The download is about 7 GB. Progress names each file. Interrupted downloads resume on retry. Models are optional for import and editing. To put originals on a separate drive, open Tools → Cache Manager → Cache location and choose the parent folder. Restart to activate it. Existing caches remain available when their location is selected again. Installed models load from disk offline. In Model Manager, Load and Unload control resident weights. Choose Use selected model to change the generation panel.

## Make an image

1. Load the LLMBAZ preset. It sets the output to 220 × 900, seed to 123456, pixel strength to 55, ink to #d8d0c0 and paper to #111111.
2. Generate, or choose File → Import image. SDXL Auto typically selects 640 × 1600 for this panel on a 32 GB GPU. Set Manual to use explicit internal dimensions. Auto limits the internal aspect ratio and selects a smaller pixel budget on lower-memory cards. Cover crops centrally. Contain pads the image. Auto fitting uses centered cover.
3. Change ink, paper, tonal settings or effects. Drag across Split to move the comparison boundary. Luminance shows the fitted grayscale image after tonal adjustments. Fit scales the display. 100%, 2×, 4× and 8× use nearest-neighbor display. Choose Cancel generation to interrupt at a denoising step. After a CUDA memory failure, select offload or smaller manual dimensions. CPU inference requires explicit selection.
4. Export PNG. Each export writes a lossless 1-bit indexed PNG, embedded settings and a JSON manifest with a checksum. The exporter decodes and checks dimensions, colors and opacity. Choose File → Export original to copy the unprocessed PNG.
5. Save a .ditherproj. Keep the SHA-256-named PNG alongside it. The project can move between computers, or reopen after clearing cached originals or uninstalling the model. Version 1 readers reject unsupported future schemas.

The cache stores a new immutable entry for every fresh inference, including forced regeneration. Identical requests reuse an integrity-checked entry. The key includes effective prompts, revision, seed, scheduler, internal dimensions, runtime versions, device and precision. Changing the output size *during editing* only fits the existing image. Explicitly starting a new generation with Auto resolution may select a different internal size. Exact reuse comes from the cached PNG. A seed alone cannot ensure identical pixels across runtime, hardware and model changes.

The CPU pipeline fits and composites the source, adjusts luminance, applies seeded distress, quantizes on the virtual grid, then maps bits to the two palette entries. Pixel block size defaults to 1 + round(11 × strength / 100). Strength zero disables that effect. Bayer scales the ordered threshold offsets. Floyd scales error propagation. Threshold interpolates between midpoint and the chosen cutoff. Grunge scales seeded ink loss and scratches. Pixel grid edges are extended to full blocks and cropped after nearest-neighbor reconstruction. No export applies filtering after palette mapping.

## Console and daily pair

Examples are PowerShell single-line commands from the distribution directory. For multiline PowerShell commands use a backtick as the last character on each continued line. Quote paths and hex colors.

~~~powershell
.\dthr-cli.exe health --json
.\dthr-cli.exe models list --json
.\dthr-cli.exe models install sdxl-base --confirm
.\dthr-cli.exe generate --preset LLMBAZ --seed 123456 --output ".\daily panel.png" --json
.\dthr-cli.exe process --input ".\original.png" --effect bayer --matrix 8 --foreground "#d8d0c0" --background "#111111" --output ".\proof.png" --json
.\dthr-cli.exe process --original-id ORIGINAL_ID --effect pixel --output ".\new proof.png" --json
.\dthr-cli.exe pair --issue 2026-11-07 --theme "Mechanical garden" --left "roots" --right "seed pods" --seed 123456 --json
~~~

Use `--help` on each command. Exit 0 indicates success, 2 indicates invalid input or usage, 1 indicates a runtime failure, and 130 indicates interruption. JSON results go to stdout and failures to stderr. Batch generation uses consecutive seeds. Each pair derives seeds from date, base seed and side, stages both outputs, validates the images and publishes a complete date directory by rename. Retrying a failure reuses completed originals. A completed issue with a different request requires a different output directory. The CLI is directly usable by local agents.

## Hermes and the HTTP API

Run `dthr-cli.exe serve`. The default listener is 127.0.0.1:7819. Use the bearer token shown under Tools → Agent API settings or read api.token inside the data folder. Restrict access to this folder to your user account. Every endpoint requires the same token. Put importable images in the data folder's imports directory. API output paths are relative to exports. Model installation is available only through the GUI or explicit local CLI command.

~~~powershell
$headers = @{ Authorization = "Bearer $((Get-Content -Raw '.\api.token').Trim())" }
$base = 'http://127.0.0.1:7819'
Invoke-RestMethod "$base/v1/health" -Headers $headers
$body = @{ preset='LLMBAZ'; seed=123456; output_name='panel.png' } | ConvertTo-Json
$job = Invoke-RestMethod "$base/v1/generations" -Method Post -Headers $headers -ContentType 'application/json' -Body $body
Invoke-RestMethod ($base + $job.status_url) -Headers $headers
~~~

Run the token example from the configured data directory. Hermes can use the same requests, poll the returned status URL until completed and read output_path from shared local storage. GET /v1/models, /v1/presets and /v1/cache discover available resources. POST /v1/imports takes input relative to imports. POST /v1/process accepts original_id or input, output_name, width, height and a postprocess object. It returns a job ID. POST /v1/jobs/{id}/cancel requests cancellation. One generation worker and two CPU workers serve up to 16 pending/running jobs. Running jobs time out after 30 minutes with best-effort interruption. Job history is in memory for this server session.

For Tailscale, start `dthr-cli.exe serve --host YOUR_TAILSCALE_IP` and configure the tailnet ACL and Windows firewall for the chosen interface. Supply the bearer token over that encrypted connection. To download a finished PNG, send an authenticated GET to /v1/exports/{output_name}, including any relative subdirectory. The server also returns a local output_path for agents on the same machine.

## Model provenance

SDXL uses Stability AI's pinned Diffusers checkpoint, fp16 safetensors, effective classifier-free negative prompting and its native scheduler, Euler or DPM++. Read the [model card and CreativeML Open RAIL++-M license](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/tree/462165984030d82259a11f4367a4eed129e94a7b).

The optional distilled Klein 4B adapter uses the official Flux2KleinPipeline and enforces guidance 1, the model scheduler and an empty negative prompt. Select four inference steps for the distilled checkpoint. This adapter has not undergone a real weights/inference acceptance run. See the [pinned model repository and Apache-2.0 license](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/tree/e7b7dc27f91deacad38e78976d1f2b499d76a294).

## Develop and build

Use 64-bit CPython 3.13.5 and a project-local virtual environment. Install requirements.lock using the PyTorch cu128 index for torch and torchvision, and PyPI for other dependencies. Run these commands from the project root.

~~~powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m dthr --data-dir data gui
.\.venv\Scripts\python packaging\build.py
.\dist\DTHR\dthr-cli.exe health --json
~~~

The builder copies the interpreter, standard library, DLLs, locked site-packages and application into dist/DTHR. It compiles GUI and CLI launchers with the Windows Framework64 C# compiler. Use a clean output directory for release builds. Model weights and user originals remain in the data folder. Generated PNGs, project companions, logs and manifests can be inspected with standard Windows tools.


## Release verification

The desktop and console executables were exercised on the target RTX 5090. The release checks launch a visible native main window, close it normally, force an offline SDXL generation, inspect the PNG after decoding, save and reopen the project, start the packaged HTTP server, reuse the cached original, change the processing effect and download the resulting image. The checks run the packaged programs with only Windows System32 on their executable search path. A separate clean Windows installation has not been tested.

| Milestone | Implementation and verification |
| --- | --- |
| M1 · Processing | `dthr/effects.py` implements the tonal, distress, pixel-grid, Bayer, Floyd–Steinberg and threshold stages. `tests/test_effects.py` exercises gradients, fine lines, seeded noise and a photograph, including narrow and odd dimensions. |
| M2 · Desktop | `dthr/gui.py` and `dthr/widgets.py` use asynchronous Qt previews, persistent presets and versioned projects. `tests/test_gui.py` exercises editing, undo, project reopening and visible errors. The native Split view was inspected with a generated original. |
| M3 · Generation | `dthr/models.py`, `dthr/storage.py` and `dthr/core.py` install pinned weights, run inference and preserve originals. The default SDXL panel was generated at 640 × 1600 and exported at 220 × 900. The original shows a central botanical composition through most of its height. The seven-pixel blocks visibly simplify the fine engraving. |
| M4 · Agents | `dthr/cli.py`, `dthr/api.py` and `dthr/jobs.py` expose the same services. Tests cover authentication, path containment, cancellation, serialized generation and independent processing. The packaged CLI produced the two-panel issue for 2026-11-07. The live HTTP test used an authenticated local client. Hermes itself was not connected. |
| M5 · Distribution | `packaging/build.py` and `packaging/Launcher.cs` build the tested Windows folder distribution. `tests/test_packaged.py` distinguishes optional real-GPU acceptance from mocked adapter tests. The optional Klein adapter remains unverified with real weights. |

At 220 × 900, three CPU runs on the generated 640 × 1600 original averaged 9 ms for pixelation, 9 ms for Bayer and 50 ms for Floyd–Steinberg. These timings cover fitting and processing before Qt presentation.

Set `DTHR_RELEASE_TEST=1` before pytest to include the packaged executable and real GPU tests. This requires the SDXL installation under the project's data directory. The test creates fresh originals, exports and a portable sample project. The remaining tests use temporary storage and require no model download. The camera fixture is Lav Varshney's CC0 photograph from scikit-image v0.25.2. [Image provenance](https://scikit-image.org/docs/stable/api/skimage.data.html#skimage.data.camera)

