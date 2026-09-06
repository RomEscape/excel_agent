"""Build the Python sidecar executable and copy it to src-tauri/binaries/.

Usage:
    cd services/sidecar && uv run --extra dev python build_sidecar.py

This script mirrors the release CI (.github/workflows/release.yml "사이드카 바이너리 빌드"):
1. Nuitka `--module` compiles the office_claw_sidecar package into build-mod/ (skipped if present)
2. PyInstaller runs sidecar-hardened.spec (the spec REQUIRES step 1's output)
3. dist/office-claw-sidecar[.exe] is copied to src-tauri/binaries/ as
   office-claw-sidecar-<target-triple>[.exe] — the name tauri.conf.json externalBin expects

Prerequisites (dev extra has both): pyinstaller, nuitka (+ MSVC on Windows / clang on macOS).
dev 모드(npm run tauri:dev)에는 이 바이너리가 필요 없다 — 사이드카는 venv 소스로 뜬다.
"""

import platform
import shutil
import subprocess
import sys
from pathlib import Path

SIDECAR_DIR = Path(__file__).parent
# 모노레포: services/sidecar → 레포 루트(parent.parent) → apps/desktop/src-tauri/binaries
TAURI_BIN_DIR = SIDECAR_DIR.parent.parent / "apps" / "desktop" / "src-tauri" / "binaries"
BUILD_MOD_DIR = SIDECAR_DIR / "build-mod"


def _target_triple() -> str:
    """Tauri externalBin 파일명에 붙는 타깃 트리플(release.yml matrix.sidecar_suffix와 같은 값)."""
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows":
        return "x86_64-pc-windows-msvc"
    if system == "Darwin":
        return "aarch64-apple-darwin" if machine in {"arm64", "aarch64"} else "x86_64-apple-darwin"
    return "x86_64-unknown-linux-gnu"


def _nuitka_module_present() -> bool:
    return any(BUILD_MOD_DIR.glob("office_claw_sidecar.*.so")) or any(
        BUILD_MOD_DIR.glob("office_claw_sidecar.*.pyd")
    )


def build() -> None:
    print("=== Building Office Claw Sidecar ===")

    # 1. Nuitka --module — sidecar-hardened.spec:31-40 이 build-mod/ 산출물을 요구한다.
    #    예전엔 이 단계가 없어서 새 PC 에서 PyInstaller 가 "Run the Nuitka --module build first."
    #    로 죽었고, setup.ps1 이 여기서 멈춰 모델 준비까지 못 갔다(2026-09-06 실측).
    if _nuitka_module_present():
        print(f"Nuitka module already in {BUILD_MOD_DIR.name}/ — skipping compile (delete the dir to rebuild)")
    else:
        print("Running Nuitka --module (office_claw_sidecar) ...")
        subprocess.run(
            [
                sys.executable, "-m", "nuitka", "--module", "office_claw_sidecar",
                "--include-package=office_claw_sidecar",
                "--python-flag=-OO",
                f"--output-dir={BUILD_MOD_DIR.name}",
            ],
            cwd=str(SIDECAR_DIR),
            check=True,
        )

    # 2. PyInstaller
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

    # 3. dist/office-claw-sidecar[.exe] → binaries/office-claw-sidecar-<triple>[.exe]
    #    spec 의 name 은 "office-claw-sidecar"(접미사 없음)라 예전 glob("office-claw-sidecar-*")은
    #    아무것도 못 찾고 exit 1 이었다. CI 처럼 트리플을 붙여 복사한다.
    dist_dir = SIDECAR_DIR / "dist"
    ext = ".exe" if platform.system() == "Windows" else ""
    built = dist_dir / f"office-claw-sidecar{ext}"
    if not built.exists():
        print(f"ERROR: PyInstaller output not found: {built}")
        sys.exit(1)

    TAURI_BIN_DIR.mkdir(parents=True, exist_ok=True)
    dest = TAURI_BIN_DIR / f"office-claw-sidecar-{_target_triple()}{ext}"
    shutil.copy2(built, dest)
    print(f"Copied: {built.name} -> {dest}")

    print("=== Build complete ===")


if __name__ == "__main__":
    build()
