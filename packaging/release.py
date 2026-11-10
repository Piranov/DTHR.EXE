"""Create the Windows release assets from a built dist/DTHR using Inno Setup 7."""

import argparse
import hashlib
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist/DTHR"
OUTPUT = ROOT / "build/release"
URL = "https://github.com/Piranov/DTHR.EXE/releases/download"
LIMIT = 950 * 1024**2


def pack(group):
    number, paths = group
    name = f"DTHR-runtime-x64-{number:02d}.zip"
    target = OUTPUT / name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for path in paths:
            archive.write(path, path.relative_to(DIST))
    with target.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    return name, checksum, sum(p.stat().st_size for p in paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("compiler", type=Path)
    args = parser.parse_args()
    from runpy import run_path

    version = run_path(str(ROOT / "dthr/__init__.py"))["__version__"]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if not (DIST / "runtime/pythonw.exe").is_file():
        parser.error("Run packaging/build.py first")
    groups = [[]]
    total = 0
    for path in sorted((DIST / "runtime").rglob("*")):
        if not path.is_file() or path.suffix == ".pyc" or "__pycache__" in path.parts:
            continue
        size = path.stat().st_size
        if groups[-1] and total + size > LIMIT:
            groups.append([])
            total = 0
        groups[-1].append(path)
        total += size
    lines = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for name, checksum, size in pool.map(pack, enumerate(groups, start=1)):
            common = f'DestDir: "{{app}}"; ExternalSize: {size}; Hash: "{checksum}"'
            flags = (
                "external ignoreversion extractarchive recursesubdirs createallsubdirs"
            )
            local = f"LocalAsset('{name}')"
            lines.extend(
                [
                    f'Source: "{{src}}\\{name}"; {common}; Flags: {flags}; Check: {local}',
                    f'Source: "{URL}/v{version}/{name}"; DestName: "{name}"; {common}; Flags: {flags} download; Check: not {local}',
                ]
            )
            print(name, checksum, flush=True)
    (OUTPUT / "runtime-files.iss").write_text("\n".join(lines), encoding="utf-8")
    subprocess.run(
        [
            str(args.compiler),
            "/Qp",
            f"/DAppVersion={version}",
            str(ROOT / "packaging/windows.iss"),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
