# DTHR.EXE user guide

## Install on Windows

Download **DTHR-Windows-x64-Setup.exe** from the [latest release](https://github.com/Piranov/DTHR.EXE/releases/latest). Run it and choose an installation folder. The roughly 75 MB installer contains the editor, CLI and HTTP API. You can install it offline and dither your own images immediately. An upgrade over a working 1.0 installation can reuse its existing GPU libraries.

Open **DTHR.EXE** from Start and choose a folder for originals, projects, exports and optional model weights. **Tools → Storage location** changes the default for the next launch. **Tools → Cache Manager → Cache location** can put originals on a separate drive. For scripts, set `DTHR_DATA` or pass `--data-dir PATH` before the CLI command.

## Make a print

1. Choose **File → Import image** and open a PNG, JPEG or WebP. To generate an image instead, select an installed model, enter a prompt and click **Generate**.
2. Set the final width and height. **Cover** crops centrally and **Contain** pads around the image. Generation resolution controls the size of a new original. Editing the output dimensions reuses the current original.
3. Pick ink and paper colors. Use **Bayer** for an ordered dot pattern, **Floyd** for error diffusion, **Pixel** for hard-edged blocks, **Threshold** for solid shapes or **Grunge** for distressed ink. **Luminance** shows your tonal adjustments. In **Split** view, drag the divider to compare the fitted original and the print.
4. Choose **Export PNG**. The app checks the dimensions, palette and opacity after encoding. The adjacent JSON manifest records settings and checksums. **File → Export original** saves a separate copy of the source.
5. Save a `.ditherproj` to resume editing. When moving a project, keep the checksum-named PNG beside it. Reopening uses the saved image and requires no model installation.

**Vertical Panel** is an editable preset for 220 × 900 output. At its default pixel strength of 55, each full cell is seven output pixels wide. Switch effects or save another preset to use other dimensions and palettes.

## Local generation

Choose **Generate** to enable local generation. The first use offers the generation runtime (about 3 GB), then asks separately for any missing model weights. The app shows progress and continues the pending request when installation finishes. Verified archives are reused on retry. You can also install the runtime ahead of time under **Tools → Model Manager → Install generation support**.

For automation, run `dthr-cli.exe runtime status --json` to check availability and `dthr-cli.exe runtime install --confirm` to opt in. For offline setup, put all the matching generation ZIP files from the release in one folder and pass `--from-dir "C:\Downloads\DTHR"` to the install command. The app verifies each archive against checksums bundled with the editor. Model weights are separate. The server never installs generation code from an API request.

**Tools → Model Manager** lists available adapters, installed weights, disk usage, licenses and supported controls. Select a model and confirm the download. The selected adapter determines which settings take effect. A preset stores its model choice along with generation and processing settings.

The app caches each generated original with its exact parameters, model revision and checksum. An identical generation request can reuse the cached image. **Force generation** creates a fresh original. Seeds help repeat a composition, but exact pixel reuse comes from the cached PNG. Choose a smaller internal resolution or offloading if GPU memory is limited.

### Supported models in version 1.1

| Model | Support |
| --- | --- |
| SDXL Base (`sdxl-base`) | Verified on an RTX 5090 with the optional CUDA 12.8 runtime. Approximately 7 GB of weights. Supports negative prompts, guidance and a choice of scheduler. [Model card and license](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/tree/462165984030d82259a11f4367a4eed129e94a7b) |
| FLUX.2 Klein 4B (`flux2-klein-4b`) | Experimental adapter. Uses fixed guidance, its own scheduler and an empty negative prompt. Real-weight inference remains unverified. [Model card and license](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/tree/e7b7dc27f91deacad38e78976d1f2b499d76a294) |

## Command line

Run these PowerShell examples from the installation folder. Change the input path to a local image.

~~~powershell
.\dthr-cli.exe process --input ".\original.png" --effect bayer --matrix 4 --output ".\print.png" --json
.\dthr-cli.exe presets list --json
.\dthr-cli.exe models list --json
.\dthr-cli.exe generate --preset "Vertical Panel" --seed 123456 --output ".\panel.png" --json
.\dthr-cli.exe pair --issue 2026-11-07 --theme "Mechanical garden" --left "roots" --right "seed pods" --json
~~~

Use `--help` on any command. JSON results go to stdout and errors go to stderr. The pair command derives separate seeds for left and right and publishes the date folder only after both exports pass validation.

## HTTP API

Start the server with `dthr-cli.exe serve`. By default it listens on `127.0.0.1:7819`. All requests use a bearer token from **Tools → Agent API settings** or the data folder's `api.token`. Any HTTP client can use the API.

To process an image, place it in the data folder's `imports` directory. Run this example from the data folder, replacing `original.png` with its filename.

~~~powershell
$base = 'http://127.0.0.1:7819'
$headers = @{ Authorization = 'Bearer ' + (Get-Content -Raw '.\api.token').Trim() }
$body = @{ input='original.png'; output_name='print.png'; postprocess=@{ effect='bayer' } } | ConvertTo-Json
$job = Invoke-RestMethod "$base/v1/process" -Method Post -Headers $headers -ContentType 'application/json' -Body $body
do {
    Start-Sleep -Milliseconds 250
    $result = Invoke-RestMethod ($base + $job.status_url) -Headers $headers
} until ($result.status -in 'completed','failed','cancelled')
if ($result.status -eq 'completed') {
    Invoke-WebRequest "$base/v1/exports/print.png" -Headers $headers -OutFile '.\print.png'
} else {
    $result.error
}
~~~

For generation, discover presets and model capabilities through `GET /v1/presets` and `GET /v1/models`, then submit `POST /v1/generations` with a preset and output name. Poll the returned status URL in the same way. Reprocess a cached source by passing its `original_id` to `POST /v1/process`. Request cancellation through `POST /v1/jobs/{id}/cancel`. Output names are relative to the exports folder.

For remote use, bind explicitly to a private interface with `serve --host PRIVATE_IP`. Use an encrypted private network and restrict incoming connections to authorized clients.

## Build from source

Use 64-bit CPython 3.13.5 on Windows. The editor includes Qt. The generation component supplies the locked CUDA-enabled PyTorch runtime. From the repository root in PowerShell:

~~~powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python -m pip install -r requirements.lock
.\.venv\Scripts\python -m dthr gui
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python packaging\build.py
~~~

The folder distribution is written to `dist/DTHR`. Keep the entire folder together. To build the release installer, run `packaging/release.py` with the path to the Inno Setup 7 compiler. The standalone editor installer and optional generation archives are written to the versioned directory under `build/release`.

The optional packaged GPU tests require the verified model and target hardware. The desktop, CLI, API, effects and cache have separate regression tests. The camera fixture is Lav Varshney's CC0 photograph from scikit-image. [Fixture provenance](https://scikit-image.org/docs/stable/api/skimage.data.html#skimage.data.camera)
