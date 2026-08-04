import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("EXCEL_LIVE_ENGINE", "file")

from office_claw_sidecar.services.excel_live_file_service import FileExcelLiveService  # noqa: E402

out = open(Path(sys.argv[2]), "w", encoding="utf-8")
source = Path(sys.argv[1])


def report(tag: str, path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        drawings = [n for n in names if re.fullmatch(r"xl/drawings/drawing\d+\.xml", n)]
        out.write(f"===== {tag} =====\n")
        for drawing in sorted(drawings):
            body = zf.read(drawing).decode("utf-8", "replace")
            used = sorted(set(re.findall(r'r:(?:id|embed)="([^"]+)"', body)))
            rels_name = drawing.replace("xl/drawings/", "xl/drawings/_rels/") + ".rels"
            declared: list[str] = []
            if rels_name in names:
                rels = zf.read(rels_name).decode("utf-8", "replace")
                declared = sorted(set(re.findall(r'Id="([^"]+)"', rels)))
            missing = [rid for rid in used if rid not in declared]
            out.write(f"{drawing}: 참조={used} 선언={declared} 끊긴참조={missing}\n")


report("원본", source)

target = source.with_name("_drawcheck.xlsx")
shutil.copy2(source, target)
svc = FileExcelLiveService()
svc.create_sheet(str(target), "새시트확인")
report("시트 추가 후", target)
target.unlink(missing_ok=True)
out.close()
