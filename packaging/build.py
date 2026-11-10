"""Build an isolated Windows x64 directory distribution from the locked runtime."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "DTHR"
RUNTIME = DIST / "runtime"


def copy_changed(src, dst):
    source, target = Path(src), Path(dst)
    if (
        target.exists()
        and source.stat().st_mtime_ns == target.stat().st_mtime_ns
        and source.stat().st_size == target.stat().st_size
    ):
        return str(target)
    return shutil.copy2(source, target)


def main():
    if sys.platform != "win32" or sys.prefix == sys.base_prefix:
        raise SystemExit("Run with the project's Windows virtual environment")
    base = Path(sys.base_prefix)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    for pattern in ("python*.exe", "*.dll", "LICENSE.txt"):
        for f in base.glob(pattern):
            shutil.copy2(f, RUNTIME / f.name)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(base / "DLLs", RUNTIME / "DLLs", dirs_exist_ok=True, ignore=ignore)
    shutil.copytree(
        base / "Lib",
        RUNTIME / "Lib",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "site-packages",
            "__pycache__",
            "test",
            "tests",
            "idlelib",
            "tkinter",
            "ensurepip",
        ),
    )
    site = Path(sys.prefix) / "Lib/site-packages"
    shutil.copytree(
        site,
        RUNTIME / "Lib/site-packages",
        dirs_exist_ok=True,
        ignore=ignore,
        copy_function=copy_changed,
    )
    (RUNTIME / "python313._pth").write_text(
        "DLLs\nLib\nLib/site-packages\n..\nimport site\n", encoding="ascii"
    )
    shutil.copytree(ROOT / "dthr", DIST / "dthr", dirs_exist_ok=True, ignore=ignore)
    for file in ("requirements.lock", "README.md"):
        shutil.copy2(ROOT / file, DIST / file)
    shutil.copytree(ROOT / "docs", DIST / "docs", dirs_exist_ok=True)
    compiler = (
        Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    )
    for desktop, name in [(True, "DTHR.EXE"), (False, "dthr-cli.exe")]:
        subprocess.run(
            [
                str(compiler),
                "/nologo",
                "/optimize+",
                "/platform:x64",
                "/r:System.Windows.Forms.dll",
                "/target:winexe" if desktop else "/target:exe",
                "/define:DESKTOP" if desktop else "/define:CLI",
                "/out:" + str(DIST / name),
                str(ROOT / "packaging/Launcher.cs"),
            ],
            check=True,
        )
    if (ROOT / "data/models/sdxl-base/installed.json").exists():
        (DIST / "storage-location.txt").write_text("../../data", encoding="utf-8")
    print(DIST)


if __name__ == "__main__":
    main()
