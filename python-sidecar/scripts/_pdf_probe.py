import os
import sys
from pathlib import Path

os.environ.setdefault("EXCEL_LIVE_ENGINE", "file")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.services.excel_live_file_service import FileExcelLiveService  # noqa: E402

out = open(Path(os.environ["TEMP"]) / "_pdf.txt", "w", encoding="utf-8")
svc = FileExcelLiveService()
try:
    result = svc.export_pdf(sys.argv[1], sheet_name="Dashboard")
    out.write(f"성공: {result}\n")
except Exception as exc:  # noqa: BLE001
    out.write(f"실패: {exc}\n")
out.close()
