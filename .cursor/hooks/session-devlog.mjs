/**
 * 세션 시작: 개발일지 최근 항목을 컨텍스트에 넣는다.
 * 커밋 훅은 "기록했는가"만 본다. 이 훅은 "작업 전에 현 상황을 읽었는가"를 담당한다.
 */
import { parsePayload, readStdin, workspaceRoot, buildSessionContext, clearDirty } from "./devlog-lib.mjs";

const payload = parsePayload(readStdin());
const root = workspaceRoot(payload);
clearDirty(root);
process.stdout.write(JSON.stringify({ additional_context: buildSessionContext(root) }));
