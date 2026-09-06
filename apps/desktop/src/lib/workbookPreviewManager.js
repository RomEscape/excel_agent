/**
 * workbookPreviewManager — 앱 안 통합문서 미리보기의 액션 소유자.
 *
 * 무엇을 보여 줄지는 excelTargetManager(대상 통합문서)가 정하고, 여기는 그 통합문서의
 * 값 스냅샷을 사이드카에서 받아 store 에 넣는다. 명령이 끝날 때마다·대상이 바뀔 때마다
 * 다시 부른다. 실패는 표시용이라 던지지 않는다.
 */
import { excelLivePreview } from "@/lib/api";
import { getExcelTarget } from "@/lib/excelTargetManager.js";
import { toUserMessage } from "@/lib/errorMessages";
import useWorkbookPreviewStore from "@/store/workbookPreviewStore";

const store = () => useWorkbookPreviewStore.getState();

/** 동시에 여러 곳이 부르면 왕복은 한 번만. */
let inflight = null;

/**
 * 현재 대상 통합문서의 스냅샷을 다시 받는다.
 *
 * @param {{ sheet?: string, workbookId?: string }} [opts]
 *   sheet — 보고 싶은 시트(생략하면 store 의 선택 → 활성 시트).
 *   workbookId — 생략하면 excelTargetManager 의 대상.
 * @returns {Promise<object|null>} 응답(실패면 null)
 */
export async function refreshWorkbookPreview(opts = {}) {
  if (inflight) return inflight;
  const target = getExcelTarget();
  const workbookId = opts.workbookId ?? target.workbookId ?? "";
  const sheet = opts.sheet ?? store().sheet;
  if (!workbookId && !target.workbookName) {
    store().setData(null);
    return null;
  }
  inflight = (async () => {
    store().setLoading(true);
    try {
      const data = await excelLivePreview({
        workbookId: workbookId || target.workbookName,
        sheetName: sheet || undefined,
        maxRows: 200,
        maxCols: 40,
      });
      store().setData(data);
      // 사이드카가 실제로 고른 시트를 store 에 반영해 탭 하이라이트가 어긋나지 않게 한다.
      if (data?.sheet && data.sheet !== store().sheet) store().setSheet(data.sheet);
      return data;
    } catch (err) {
      store().setError(toUserMessage(err, "통합문서를 읽지 못했습니다."));
      return null;
    } finally {
      store().setLoading(false);
      inflight = null;
    }
  })();
  return inflight;
}

/** 탭 클릭 — 시트를 바꾸고 바로 다시 읽는다. */
export function selectPreviewSheet(sheet) {
  store().setSheet(String(sheet || ""));
  return refreshWorkbookPreview({ sheet: String(sheet || "") });
}

export function setWorkbookPreviewOpen(open) {
  store().setOpen(open);
}

/** 대상 통합문서가 바뀌면 시트 선택을 버리고 활성 시트부터 본다. */
export function resetWorkbookPreviewForNewTarget() {
  store().setSheet("");
  store().setData(null);
  return refreshWorkbookPreview({ sheet: "" });
}
