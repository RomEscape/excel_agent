"""Excel COM으로 통합문서를 열어보고 결과만 한 줄로 알린다.

openpyxl로는 읽히는데 Excel에서는 안 열리는 손상을 잡는 용도.
"""

from __future__ import annotations

import sys


def main() -> int:
    path = sys.argv[1]
    try:
        import win32com.client  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        print("검사 불가(win32com 없음)")
        return 0

    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        book = excel.Workbooks.Open(path)
        book.Close(SaveChanges=False)
        print("열림")
    except Exception as exc:  # noqa: BLE001
        print(f"열기 실패: {str(exc)[:100]}")
    finally:
        excel.Quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
