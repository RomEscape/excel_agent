/**
 * 파일 수정 후: 코드가 바뀌면 dirty 표시, 개발일지를 고치면 해제.
 * git이 없어도 세션 종료 훅이 미기록을 잡을 수 있게 한다.
 */
import { noteFileEdit, parsePayload, readStdin, workspaceRoot } from "./devlog-lib.mjs";

const payload = parsePayload(readStdin());
const root = workspaceRoot(payload);
const filePath = payload?.file_path || payload?.filePath || "";
if (filePath) {
  noteFileEdit(root, filePath);
}
process.stdout.write("{}");
