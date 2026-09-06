// 앱 안 통합문서 미리보기를 구독한다. 상태는 store, 액션은 manager 가 갖는다(CLAUDE.md §4).
//
// 대상 통합문서(excelTargetManager)가 바뀌면 활성 시트부터 다시 읽는다. 명령 뒤 갱신은
// WorkspacePage 가 결과를 받는 자리에서 refreshWorkbookPreview() 를 부른다.
import { useEffect, useRef } from "react";

import useWorkbookPreviewStore from "@/store/workbookPreviewStore";
import { resetWorkbookPreviewForNewTarget } from "@/lib/workbookPreviewManager";

export function useWorkbookPreview(targetWorkbookName) {
  const data = useWorkbookPreviewStore((s) => s.data);
  const sheet = useWorkbookPreviewStore((s) => s.sheet);
  const loading = useWorkbookPreviewStore((s) => s.loading);
  const error = useWorkbookPreviewStore((s) => s.error);
  const open = useWorkbookPreviewStore((s) => s.open);
  const updatedAt = useWorkbookPreviewStore((s) => s.updatedAt);

  const lastTarget = useRef("");
  useEffect(() => {
    const name = String(targetWorkbookName || "");
    if (name === lastTarget.current) return;
    lastTarget.current = name;
    if (name) resetWorkbookPreviewForNewTarget();
  }, [targetWorkbookName]);

  return { data, sheet, loading, error, open, updatedAt };
}

export default useWorkbookPreview;
