/**
 * documentManager — 워크스페이스 문서 도메인의 액션 소유자.
 *
 * 홈 화면의 문서 액션 바(`새로운 문서 생성` / `문서 업로드` / `문서 삭제`)와
 * 문서 카드 열기가 전부 여기를 부른다. UI는 invoke를 직접 부르지 않는다
 * (CLAUDE.md 안티패턴).
 *
 * 표시 모델(카드 목록·`3일 전`·더보기 개수)은 lib/documents.js가 순수하게 만든다.
 * 이 파일은 부수효과(IPC 호출 + store 갱신)만 맡는다.
 */
import useDocumentStore from "@/store/documentStore";
import { toUserMessage } from "@/lib/errorMessages";
import {
  workspaceListFiles,
  workspaceCreateExcelFile,
  workspaceDeleteFile,
  workspaceWriteFile,
  workspaceWriteFileBinary,
  openWorkspaceFile,
} from "@/lib/api";

const store = () => useDocumentStore.getState();

/**
 * 텍스트로 안전하게 읽을 수 있는 확장자 (UTF-8 가정).
 * 그 외(.xlsx/.pdf/.docx/.pptx/...)는 base64 바이너리 업로드로 간다.
 */
const TEXT_EXT = new Set([
  "txt", "md", "csv", "json", "py", "js", "ts", "jsx", "tsx",
  "yaml", "yml", "toml", "sh", "html", "css", "log", "xml",
]);

function extOf(name) {
  const dot = String(name || "").lastIndexOf(".");
  return dot > 0 ? String(name).slice(dot + 1).toLowerCase() : "";
}

/**
 * ArrayBuffer → base64.
 *
 * 8KB씩 끊어 넘긴다. 통째로 String.fromCharCode(...bytes)를 하면 큰 파일에서
 * 인자 개수 상한에 걸려 RangeError로 죽는다.
 */
function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const CHUNK = 0x2000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

/** 워크스페이스 루트의 파일 목록을 다시 읽는다. */
// 앱이 사이드카보다 먼저 뜨는 첫 구동에서는 최초 조회가 반드시 진다 — 재시도가
// 없으면 "문서 목록을 불러오지 못했습니다" 배너가 박제된다(2026-09-01 첫 구동
// 실측: 사이드카는 2번째 헬스체크에 떴는데 배너는 그대로). 부팅 레이스 한정으로
// 짧게 재시도하고, 성공하면 상한을 되돌린다.
let documentsRetryTimer = 0;
let documentsRetriesLeft = 15;

export async function refreshDocuments(path) {
  const { setFiles, setLoading, setError } = store();
  setLoading(true);
  if (typeof window !== "undefined") window.clearTimeout(documentsRetryTimer);
  try {
    const data = await workspaceListFiles(path);
    setFiles(data?.files, data?.workspace);
    setError("");
    documentsRetriesLeft = 15;
  } catch (err) {
    setFiles([], "");
    setError(toUserMessage(err, "문서 목록을 불러오지 못했습니다."));
    if (documentsRetriesLeft > 0 && typeof window !== "undefined") {
      documentsRetriesLeft -= 1;
      documentsRetryTimer = window.setTimeout(() => refreshDocuments(path), 2000);
    }
  } finally {
    setLoading(false);
  }
}

/**
 * 새 엑셀 문서를 만들고 곧바로 연다.
 *
 * @param {string} name 파일명 (.xlsx는 없으면 붙인다)
 * @returns {Promise<boolean>} 성공 여부
 */
export async function createExcelDocument(name) {
  const { setBusy, setError } = store();
  let fileName = String(name || "").trim();
  if (!fileName) {
    setError("파일 이름을 입력해 주세요.");
    return false;
  }
  if (!fileName.toLowerCase().endsWith(".xlsx")) fileName = `${fileName}.xlsx`;

  setBusy(true);
  try {
    await workspaceCreateExcelFile(fileName, "Sheet1");
    setError("");
    await refreshDocuments();
    // 만들자마자 열어준다 — 안 열면 "생성됨" 문구만 뜨고 다음 동작이 없다.
    await openWorkspaceFile(fileName).catch(() => {});
    return true;
  } catch (err) {
    setError(toUserMessage(err, "문서를 만들지 못했습니다."));
    return false;
  } finally {
    setBusy(false);
  }
}

/**
 * 사용자가 고른 파일들을 워크스페이스에 올린다.
 *
 * @param {FileList|File[]} fileList
 * @returns {Promise<number>} 성공한 개수
 */
export async function uploadDocuments(fileList) {
  const files = Array.from(fileList || []);
  if (files.length === 0) return 0;

  const { setBusy, setError } = store();
  setBusy(true);
  let uploaded = 0;
  const failed = [];

  try {
    for (const file of files) {
      try {
        if (TEXT_EXT.has(extOf(file.name))) {
          await workspaceWriteFile(file.name, await file.text());
        } else {
          const b64 = arrayBufferToBase64(await file.arrayBuffer());
          await workspaceWriteFileBinary(file.name, b64);
        }
        uploaded += 1;
      } catch {
        // 한 개가 실패해도 나머지는 계속 올린다. 어떤 게 실패했는지는 모아서 알린다.
        failed.push(file.name);
      }
    }
    setError(
      failed.length > 0 ? `업로드 실패: ${failed.join(", ")}` : ""
    );
    await refreshDocuments();
    return uploaded;
  } finally {
    setBusy(false);
  }
}

/**
 * 문서 하나를 삭제한다. 되돌릴 수 없으므로 호출 전에 확인을 받아야 한다 —
 * 확인 UI는 부르는 쪽(HomePage)이 띄운다.
 *
 * @param {string} path 워크스페이스 기준 상대 경로
 * @returns {Promise<boolean>} 성공 여부
 */
export async function deleteDocument(path) {
  if (!path) return false;
  const { setBusy, setError } = store();
  setBusy(true);
  try {
    await workspaceDeleteFile(path);
    setError("");
    await refreshDocuments();
    return true;
  } catch (err) {
    setError(toUserMessage(err, "문서를 삭제하지 못했습니다."));
    return false;
  } finally {
    setBusy(false);
  }
}

/** 문서를 OS 기본 앱으로 연다 (엑셀 파일이면 Excel). */
export async function openDocument(path) {
  const { setError } = store();
  try {
    await openWorkspaceFile(path);
    setError("");
  } catch (err) {
    setError(toUserMessage(err, "문서를 열지 못했습니다."));
  }
}
