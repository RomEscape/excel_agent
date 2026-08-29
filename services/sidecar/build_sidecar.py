"""Build the Python sidecar executable and copy it to src-tauri/binaries/.

Usage:
    python build_sidecar.py

This script:
1. Runs PyInstaller with the .spec file to create a standalone binary
2. Copies the output to src-tauri/binaries/ with the correct target-triple name
3. The binary can then be used by Tauri as an externalBin sidecar

Prerequisites:
    pip install -r requirements.txt
    pip install pyinstaller
"""

import shutil
import subprocess
import sys
from pathlib import Path

SIDECAR_DIR = Path(__file__).parent
# 모노레포: services/sidecar → 레포 루트(parent.parent) → apps/desktop/src-tauri/binaries
TAURI_BIN_DIR = SIDECAR_DIR.parent.parent / "apps" / "desktop" / "src-tauri" / "binaries"


def build() -> None:
    print("=== Building Office Claw Sidecar ===")

    # Run PyInstaller
    # office_claw_sidecar.spec은 의도적으로 삭제됐다(docs/build-and-release.md '버린 것').
    # git이 추적하는 유일한 spec은 하드닝 빌드용 sidecar-hardened.spec이다(2026-08-30 감사).
    spec_file = SIDECAR_DIR / "sidecar-hardened.spec"
    if not spec_file.exists():
        print(f"ERROR: Spec file not found: {spec_file}")
        sys.exit(1)

    print(f"Running PyInstaller with {spec_file.name}...")
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--clean", "--noconfirm",
            str(spec_file),
        ],
        cwd=str(SIDECAR_DIR),
        check=True,
    )

    # Find and copy output
    dist_dir = SIDECAR_DIR / "dist"
    exe_files = list(dist_dir.glob("office-claw-sidecar-*"))
    if not exe_files:
        print("ERROR: PyInstaller output not found in dist/")
        sys.exit(1)

    TAURI_BIN_DIR.mkdir(parents=True, exist_ok=True)

    for f in exe_files:
        dest = TAURI_BIN_DIR / f.name
        shutil.copy2(f, dest)
        print(f"Copied: {f.name} -> {dest}")

    print("=== Build complete ===")


if __name__ == "__main__":
    build()
