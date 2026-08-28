# -*- mode: python ; coding: utf-8 -*-
"""
하이브리드 사이드카 번들 — 우리 코드만 네이티브, 나머지는 PyInstaller.

배경: PyInstaller onefile 은 `.pyc` 를 담는 포장지일 뿐이라 아카이브를 풀면
우리 모듈이 디컴파일 가능한 상태로 나온다. Nuitka 로 전부 컴파일하면 막히지만
1766개 모듈 중 우리 코드는 47개(2%)뿐이라, 나머지 98%(오픈소스 의존성)를
컴파일하는 대가로 빌드 10배·기동 +2초를 치르게 된다.

그래서 `office_claw_sidecar` 패키지 하나만 Nuitka `--module` 로 `.so` 를 만들고,
PyInstaller 는 **분석은 소스로** 하되(그래야 fastapi·uvicorn·xlwings 같은 전이
의존을 빠짐없이 찾는다) **번들에는 `.so` 를 넣는다**.

파일 이름이 `office-claw-sidecar.spec` 이 **아닌** 이유: PyInstaller 를 CLI 로
(`--name office-claw-sidecar`) 돌리면 같은 이름의 spec 을 자동 생성해 이 파일을
말없이 덮어쓴다. `.gitignore` 의 `services/sidecar/*.spec` 도 그 자동 생성물을
막으려던 규칙이라, 이 파일만 예외로 추적한다.

빌드 순서(둘 다 필요하다):
    python -m nuitka --module office_claw_sidecar \
        --include-package=office_claw_sidecar --python-flag=-OO --output-dir=build-mod
    pyinstaller --noconfirm sidecar-hardened.spec   # optimize 는 spec 안에 있다
"""

import glob
import os

# 확장 모듈 파일명은 플랫폼마다 다르다 — macOS `.so`, Windows `.pyd`.
SO = glob.glob(os.path.join("build-mod", "office_claw_sidecar.*.so")) + glob.glob(
    os.path.join("build-mod", "office_claw_sidecar.*.pyd")
)
if not SO:
    raise SystemExit(
        "build-mod/office_claw_sidecar.*.{so,pyd} 가 없다. Nuitka --module 빌드를 먼저 돌릴 것."
    )
SO = SO[0]

a = Analysis(
    ["office_claw_sidecar/__main__.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["office_claw_sidecar", "uvicorn", "fastapi"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    # `python -OO` 상당 — 남는 서드파티 `.pyc` 의 docstring 을 지운다.
    # (우리 코드는 아래에서 `.so` 로 대체되므로 이 옵션과 무관하다.)
    optimize=2,
)

# 우리 패키지의 순수 파이썬 모듈을 PYZ 에서 빼고 — 이게 빠지지 않으면 `.pyc` 가
# 그대로 남아 하드닝이 무의미해진다.
removed = [e for e in a.pure if e[0] == "office_claw_sidecar" or e[0].startswith("office_claw_sidecar.")]
a.pure = TOC([e for e in a.pure if e not in removed])
print(f"[hardening] PYZ 에서 제외한 우리 모듈: {len(removed)}개")

# 그 자리에 Nuitka 컴파일 확장 모듈을 넣는다. `_MEIPASS` 최상단에 확장 모듈
# 파일명 규칙(`<이름>.cpython-<버전>-<플랫폼>.so`)으로 두면 표준 FileFinder 가
# `import office_claw_sidecar` 를 여기로 해석한다.
a.binaries += TOC([(os.path.basename(SO), SO, "BINARY")])
print(f"[hardening] 번들에 넣은 확장 모듈: {SO}")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="office-claw-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
