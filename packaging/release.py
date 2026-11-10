"""Build the standalone editor setup and optional verified generation archives."""
import argparse
import hashlib
import json
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from runpy import run_path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist/DTHR"
VERSION = run_path(str(ROOT / "dthr/__init__.py"))["__version__"]
OUTPUT = ROOT / "build/release" / ("v" + VERSION)
LIMIT = 950 * 1024**2
# Import roots and distribution metadata belonging to the inference stack.
INFERENCE = {
    "torch", "torchvision", "torchgen", "functorch", "diffusers", "accelerate",
    "transformers", "tokenizers", "safetensors", "sympy", "isympy.py", "mpmath",
    "networkx", "fsspec", "regex", "jinja2", "markupsafe", "yaml", "_yaml", "pyyaml",
}


def is_generation_file(relative):
    parts = relative.parts
    if len(parts) < 3 or parts[:2] != ("Lib", "site-packages"):
        return False
    package = parts[2].lower().split("-")[0]
    return package in INFERENCE


def pack(group):
    number, paths = group
    # GitHub lists release assets by filename. Keep the editor installer above optional downloads.
    name = f"Optional-GPU-x64-{number:02d}.zip"
    target = OUTPUT / name
    site = DIST / "runtime/Lib/site-packages"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for path in paths:
            archive.write(path, path.relative_to(site))
    with target.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    return dict(name=name, sha256=checksum, size=target.stat().st_size,
                unpacked=sum(p.stat().st_size for p in paths))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("compiler", type=Path)
    args = parser.parse_args()
    runtime = DIST / "runtime"
    if not (runtime / "pythonw.exe").is_file():
        parser.error("Run packaging/build.py first")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    groups = [[]]
    total = 0
    # Generate an exact file list so stale files from earlier builds cannot enter Setup.
    editor = []
    for path in sorted(runtime.rglob("*")):
        if not path.is_file() or path.suffix == ".pyc" or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(runtime)
        if not is_generation_file(rel):
            editor.append(f'Source: "{path}"; DestDir: "{{app}}\\runtime\\{rel.parent}"; Flags: ignoreversion')
            continue
        size = path.stat().st_size
        if groups[-1] and total + size > LIMIT:
            groups.append([])
            total = 0
        groups[-1].append(path)
        total += size
    (OUTPUT/"editor-files.iss").write_text("\n".join(editor),encoding="utf-8-sig")
    with ThreadPoolExecutor(max_workers=6) as pool:
        chunks = list(pool.map(pack, enumerate(groups,start=1)))
    (OUTPUT/"generation.json").write_text(json.dumps(dict(version=VERSION,chunks=chunks),indent=2),encoding="utf-8")
    subprocess.run([str(args.compiler),"/Qp",f"/DAppVersion={VERSION}",str(ROOT/"packaging/windows.iss")],check=True)


if __name__ == "__main__":
    main()
