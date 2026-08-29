import os
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("EXCEL_LIVE_ENGINE", "file")

from office_claw_sidecar.services.excel_live_file_service import FileExcelLiveService

out = open(Path(os.environ["TEMP"]) / "_rels.txt", "w", encoding="utf-8")
source = Path(sys.argv[1])
svc = FileExcelLiveService()

PARTS = ("xl/drawings/_rels/drawing1.xml.rels", "xl/drawings/drawing1.xml")


def dump(tag: str, path: Path) -> None:
    out.write(f"===== {tag} =====\n")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        out.write("차트/드로잉 파트: " + str(sorted(n for n in names if "chart" in n or "drawing" in n)) + "\n")
        for part in PARTS:
            if part in names:
                out.write(f"--- {part}\n{zf.read(part).decode('utf-8')}\n")


dump("원본", source)

target = source.with_name("_rels_newsheet.xlsx")
shutil.copy2(source, target)
svc.create_sheet(str(target), "새시트확인")
dump("시트 추가 후", target)
target.unlink(missing_ok=True)
out.close()
