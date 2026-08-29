"""수식 계산값 보존 캐시 — openpyxl 저장이 지워 버리는 값을 대신 들고 있는 계층.

Excel은 "=I2*J2" 같은 수식과 함께 마지막으로 계산한 값을 파일에 캐시해 둔다. openpyxl은
수식만 다시 써서 저장하므로, 우리가 파일을 한 번이라도 고치면 그 캐시가 통째로 사라진다.
그 다음 턴부터 매출 열을 읽으면 숫자가 아니라 "=I2*J2" 문자열이 나오고, 집계는 조용히
엉뚱한 열로 넘어가거나 날짜를 더해 4.8e16 같은 값을 내놓는다. 실제로 그렇게 실패했다.

그래서 편집 저장 직전에 그 시점의 계산값을 스냅샷으로 떠 두고(`capture`), 이후 읽기에서
파일 캐시가 비어 있으면 스냅샷을 대신 쓴다(`lookup`). 값이 바뀔 만한 편집이 들어오면
해당 시트 스냅샷을 버려(`invalidate_sheet`) 오래된 숫자로 답하지 않는다 — 틀린 값을 주느니
"엑셀에서 한 번 열어 저장해 달라"고 말하는 편이 낫다.

프로세스 메모리에만 산다. 사이드카가 재시작되면 스냅샷도 사라지고, 그때는 위의 안내로 떨어진다.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

# {파일 경로: {시트: {(행, 열): 계산값}}}
_STORE: dict[str, dict[str, dict[tuple[int, int], Any]]] = {}
_LOCK = threading.RLock()


def _sheet(sheet: str) -> str:
    # 호출부마다 시트 이름 대소문자가 달라도 같은 칸을 가리키게 한다.
    return str(sheet or "").strip().lower()


def _key(path: Path | str) -> str:
    try:
        return str(Path(path).resolve()).lower()
    except Exception:
        return str(path).lower()


def capture(path: Path | str, wb_data: Any, wb_formula: Any) -> int:
    """수식 셀의 현재 계산값을 스냅샷으로 저장한다. 저장한 셀 수를 돌려준다.

    `wb_data`는 data_only=True로 연 통합문서다. 값이 이미 비어 있으면(= 우리가 이전에
    저장해 캐시가 날아간 파일) 기존 스냅샷을 지우지 않고 그대로 둔다.
    """
    snapshot: dict[str, dict[tuple[int, int], Any]] = {}
    for ws_formula in getattr(wb_formula, "worksheets", []) or []:
        title = str(getattr(ws_formula, "title", "") or "")
        if not title or title not in getattr(wb_data, "sheetnames", []):
            continue
        ws_data = wb_data[title]
        cells: dict[tuple[int, int], Any] = {}
        for row in ws_formula.iter_rows():
            for cell in row:
                if not isinstance(cell.value, str) or not cell.value.startswith("="):
                    continue
                cached = ws_data.cell(row=cell.row, column=cell.column).value
                if cached is None:
                    continue
                cells[(cell.row, cell.column)] = cached
        if cells:
            snapshot[title] = cells

    if not snapshot:
        return 0
    with _LOCK:
        store = _STORE.setdefault(_key(path), {})
        for title, cells in snapshot.items():
            store[_sheet(title)] = cells
    return sum(len(cells) for cells in snapshot.values())


def lookup(path: Path | str, sheet: str, row: int, col: int) -> Any:
    with _LOCK:
        return _STORE.get(_key(path), {}).get(_sheet(sheet), {}).get((int(row), int(col)))


def has_sheet(path: Path | str, sheet: str) -> bool:
    with _LOCK:
        return bool(_STORE.get(_key(path), {}).get(_sheet(sheet)))


def invalidate_sheet(path: Path | str, sheet: str | None = None) -> None:
    """값이 바뀌었을 수 있는 시트의 스냅샷을 버린다. sheet가 없으면 파일 전체."""
    with _LOCK:
        store = _STORE.get(_key(path))
        if not store:
            return
        if sheet is None:
            store.clear()
        else:
            store.pop(_sheet(sheet), None)


def install_cells(path: Path | str, sheet: str, cells: dict[tuple[int, int], Any]) -> None:
    """정렬·중복제거처럼 행이 통째로 옮겨간 뒤, 새 좌표의 계산값을 다시 심는다.

    행이 섞였는데 스냅샷을 그대로 두면 5행 매출에 2행 값이 붙는다. 옮긴 쪽에서 직접
    (새 행, 열) → 값을 만들어 넘긴다.
    """
    if not cells:
        return
    with _LOCK:
        store = _STORE.setdefault(_key(path), {})
        store[_sheet(sheet)] = {(int(r), int(c)): v for (r, c), v in cells.items()}


def invalidate_rows(path: Path | str, sheet: str, rows: set[int] | list[int]) -> None:
    """값이 바뀐 행의 스냅샷만 버린다. 같은 행 안에서 계산되는 수식(=I2*J2)을 겨냥한다."""
    targets = {int(r) for r in rows}
    if not targets:
        return
    with _LOCK:
        cells = _STORE.get(_key(path), {}).get(_sheet(sheet))
        if not cells:
            return
        for key in [k for k in cells if k[0] in targets]:
            cells.pop(key, None)


def invalidate_other_sheets(path: Path | str, sheet: str) -> None:
    """다른 시트의 합계·대시보드 수식은 이 시트를 참조할 수 있으므로 함께 버린다."""
    with _LOCK:
        store = _STORE.get(_key(path))
        if not store:
            return
        for name in [n for n in store if n != _sheet(sheet)]:
            store.pop(name, None)


def clear_all() -> None:
    with _LOCK:
        _STORE.clear()
