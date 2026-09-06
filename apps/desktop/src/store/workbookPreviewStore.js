/**
 * workbookPreviewStore — 앱 안 통합문서 미리보기(값 스냅샷)의 상태 소유자.
 *
 * 2026-09-06 사용자: "화면 안에서 엑셀 파일 확인이 가능하게, 엑셀 옆에 대화창".
 * 데이터는 사이드카 `/excel-live/preview` 한 곳에서만 온다(lib/workbookPreviewManager.js).
 * 화면(components/workspace/WorkbookPreview.jsx)은 구독만 한다(CLAUDE.md §4).
 */
import { create } from "zustand";

const useWorkbookPreviewStore = create((set) => ({
  /** 사이드카 응답 그대로: {workbook_id, name, engine, sheets, active_sheet, sheet, range, values, truncated} */
  data: null,
  /** 사용자가 탭에서 고른 시트. 비어 있으면 활성 시트. */
  sheet: "",
  loading: false,
  error: "",
  /** 패널 표시 여부 — 대상 파일이 있으면 기본 표시. */
  open: true,
  /** 마지막으로 성공한 갱신 시각(ms). 표 헤더의 "n초 전" 표시용. */
  updatedAt: 0,

  setData: (data) => set({ data: data ?? null, error: "", updatedAt: Date.now() }),
  setSheet: (sheet) => set({ sheet: sheet ?? "" }),
  setLoading: (loading) => set({ loading: !!loading }),
  setError: (error) => set({ error: error ?? "" }),
  setOpen: (open) => set({ open: !!open }),
}));

export default useWorkbookPreviewStore;
