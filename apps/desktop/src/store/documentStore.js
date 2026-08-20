/**
 * documentStore — 워크스페이스 문서 목록의 상태 소유자.
 *
 * 홈(문서 그리드)과 워크스페이스 페이지가 같은 목록을 본다. 예전처럼 각자
 * `workspaceListFiles()`를 부르면 한쪽에서 파일을 지워도 다른 쪽은 모른 채
 * 남아 있다 (CLAUDE.md 안티패턴: "중복 fetch").
 *
 * 액션(목록 갱신·생성·업로드·삭제)은 lib/documentManager.js가 갖는다.
 */
import { create } from "zustand";

const useDocumentStore = create((set) => ({
  /** @type {Array<{name: string, path: string, size: number, modified: number, is_dir: boolean}>} */
  files: [],
  /** 워크스페이스 절대 경로 (표시용) */
  workspace: "",
  loading: false,
  /** 마지막 오류 — 사용자에게 보여줄 문장. 성공하면 비운다. */
  error: "",
  /** 진행 중 표시 — 버튼 비활성화에 쓴다. */
  busy: false,

  setFiles: (files, workspace) =>
    set({ files: Array.isArray(files) ? files : [], workspace: workspace ?? "" }),
  setLoading: (loading) => set({ loading: !!loading }),
  setError: (error) => set({ error: error ?? "" }),
  setBusy: (busy) => set({ busy: !!busy }),
}));

export default useDocumentStore;
