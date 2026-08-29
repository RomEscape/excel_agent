/**
 * Error message mapping utility.
 *
 * Converts raw technical error strings (from Rust/Python) into
 * user-friendly Korean messages for non-developer users.
 */

/**
 * @typedef {{ pattern: RegExp; message: string }} ErrorMapping
 */

/** @type {ErrorMapping[]} */
const ERROR_MAPPINGS = [
  // Network / connection errors
  {
    // Sidecar not running — connection refused on port 19532
    pattern: /Connection refused.*19532|19532.*Connection refused|connect.*19532/i,
    message: "백그라운드 서비스에 연결할 수 없습니다. 앱을 재시작해 주세요.",
  },
  {
    // Ollama not running: connection refused on port 11434
    pattern: /Connection refused.*11434|11434.*Connection refused|connect.*11434/i,
    message: "로컬 AI 엔진이 실행되지 않고 있습니다. 로컬 AI 설정에서 [자동 시작]을 눌러주세요.",
  },
  {
    pattern: /Connection refused|tcp connect error|error trying to connect/i,
    message: "백그라운드 서비스에 연결할 수 없습니다. 앱을 재시작해 주세요.",
  },
  {
    pattern: /Health check failed/i,
    message: "서비스 상태 확인에 실패했습니다. 잠시 후 다시 시도해 주세요.",
  },
  {
    pattern: /timeout|timed out/i,
    message: "요청 시간이 초과되었습니다. 네트워크 상태를 확인하고 다시 시도해 주세요.",
  },
  {
    pattern: /network|NETWORK/,
    message: "네트워크 오류가 발생했습니다. 인터넷 연결을 확인해 주세요.",
  },

  // Auth errors
  {
    pattern: /401|Unauthorized|unauthorized/,
    message: "인증에 실패했습니다. 자격증명을 확인해 주세요.",
  },
  {
    pattern: /403|Forbidden|forbidden/,
    message: "접근 권한이 없습니다. 자격증명 설정을 확인해 주세요.",
  },
  {
    pattern: /invalid.*token|token.*invalid|Invalid credentials/i,
    message: "저장된 토큰이 유효하지 않습니다. 자격증명 관리에서 토큰을 다시 저장해 주세요.",
  },


  // LLM / AI errors
  {
    pattern: /ollama|Ollama/,
    message: "로컬 AI 엔진에 연결할 수 없습니다. 설치되고 실행 중인지 확인해 주세요.",
  },
  {
    // LLM request timeout — separate from generic network timeout
    pattern: /LLM.*timeout|llm.*timed/i,
    message: "AI 응답 시간이 초과되었습니다. AI 서비스가 바쁩니다. 잠시 후 다시 시도해 주세요.",
  },
  {
    pattern: /rate.?limit|RateLimit/i,
    message: "API 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.",
  },

  // 자격증명 만료
  {
    pattern: /token.*expired|expired.*token|Token has been expired|invalid_grant/i,
    message: "인증 토큰이 만료되었습니다. 자격증명 관리에서 다시 저장해 주세요.",
  },
  {
    pattern: /refresh.*token|token.*refresh/i,
    message: "인증 세션이 만료되었습니다. 자격증명 관리에서 다시 저장해 주세요.",
  },

  // Server errors
  {
    pattern: /500|Internal Server Error/i,
    message: "서버 내부 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
  },
  {
    pattern: /404|Not Found/i,
    message: "요청한 항목을 찾을 수 없습니다.",
  },
  {
    pattern: /HTTP [45]\d{2}/,
    message: "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
  },

  // Keychain / credential store
  {
    pattern: /keychain|keyring|credential.*store/i,
    message: "OS 보안 저장소 접근에 실패했습니다. 시스템 권한을 확인해 주세요.",
  },
  {
    pattern: /not found.*credential|credential.*not found/i,
    message: "자격증명을 찾을 수 없습니다. 자격증명 관리에서 저장 여부를 확인해 주세요.",
  },

  // JSON parse / data errors
  {
    pattern: /JSON|parse.*error|invalid.*json/i,
    message: "데이터를 읽는 중 오류가 발생했습니다. 다시 시도해 주세요.",
  },

  // tool-calling 미지원 — provider가 ollama 하나뿐이라 지금은 도달 불가하지만,
  // 선택한 Ollama 모델이 tools를 지원하지 않을 때 사이드카가 같은 오류를 낸다.
  {
    pattern: /tools\(function calling\)를 지원하지 않습니다|function calling/i,
    message:
      "선택한 AI 모델이 함수 호출(tool-calling)을 지원하지 않습니다. 설정에서 다른 모델을 골라 주세요.",
  },
  // Ollama 서버 연결 실패 (사이드카가 503으로 반환)
  {
    pattern: /Ollama 서버에 연결할 수 없습니다|503/i,
    message: "로컬 AI 엔진이 실행되지 않고 있습니다. 로컬 AI 설정에서 실행 상태를 확인해 주세요.",
  },

  // Session expiry
  {
    pattern: /session.*expired|session.*not found/i,
    message: "세션이 만료되었습니다. 새 대화를 시작해 주세요.",
  },

  // File size / request too large
  {
    pattern: /413|too.*large|file.*size.*exceed/i,
    message: "파일 크기가 너무 큽니다. 더 작은 파일을 사용해 주세요.",
  },

  // Broken pipe / connection reset
  {
    pattern: /ECONNRESET|EPIPE|broken.*pipe/i,
    message: "서버와의 연결이 끊어졌습니다. 잠시 후 다시 시도해 주세요.",
  },

  // Disk full
  {
    pattern: /disk.*full|no.*space/i,
    message: "디스크 공간이 부족합니다. 불필요한 파일을 삭제해 주세요.",
  },

  // Version mismatch / upgrade required
  {
    pattern: /upgrade.*required|version.*mismatch/i,
    message: "앱 버전이 맞지 않습니다. 최신 버전으로 업데이트해 주세요.",
  },

  // File system
  {
    pattern: /permission denied|Permission denied/,
    message: "파일 접근 권한이 없습니다. 앱 권한 설정을 확인해 주세요.",
  },
  {
    pattern: /No such file|file.*not found/i,
    message: "파일을 찾을 수 없습니다.",
  },

  // Excel / file parsing
  {
    // Excel COM 인스턴스 미실행/연결 실패 (깨진 문자열 포함)
    pattern:
      /excel.*(인스턴스|instance)|실행 중인 Excel 인스턴스|활성화된 Excel 인스턴스|Excel Live 오류/i,
    message: "실행 중인 Excel을 찾지 못했습니다. Excel 파일을 먼저 열고 다시 시도해 주세요.",
  },
  {
    // write_range 파라미터 누락/형식 오류
    pattern: /values_2d|write_range.*2차원|2차원.*배열/i,
    message: "엑셀 입력 형식이 올바르지 않습니다. 셀 범위와 값을 다시 확인해 주세요.",
  },
  {
    pattern: /지원하지 않는 파일 형식|unsupported.*format|not.*xlsx|not.*csv/i,
    message: "지원하지 않는 파일 형식입니다. xlsx 또는 csv 파일을 사용해 주세요.",
  },
  {
    pattern: /파일을 읽을 수 없습니다|parse.*excel|excel.*parse|openpyxl|xlrd/i,
    message: "파일을 읽을 수 없습니다. 파일이 손상되지 않은 올바른 엑셀/CSV 파일인지 확인해 주세요.",
  },
  {
    // Stale file_id — file was cleaned up (24h expiry) or app was restarted
    pattern: /파일을 찾을 수 없습니다|file_id.*not found|HTTP 404.*excel|HTTP 404.*document/i,
    message: "업로드된 파일을 찾을 수 없습니다. 파일을 다시 업로드해 주세요.",
  },
  {
    pattern: /Excel 업로드|excel.*upload/i,
    message: "파일 업로드에 실패했습니다. 파일 크기와 형식을 확인하고 다시 시도해 주세요.",
  },
  {
    pattern: /Excel 내보내기|excel.*export/i,
    message: "Excel 파일 내보내기에 실패했습니다. 다시 시도해 주세요.",
  },
  {
    pattern: /차트 데이터|chart.*data/i,
    message: "차트 데이터를 불러올 수 없습니다. 파일에 숫자형 데이터가 포함되어 있는지 확인해 주세요.",
  },

  // Document generation / export
  {
    pattern: /지원하지 않는 문서 유형|unsupported.*doc.*type/i,
    message: "선택한 문서 유형을 지원하지 않습니다. 다른 유형을 선택해 주세요.",
  },
  {
    pattern: /문서 생성|document.*generat/i,
    message: "문서 초안 생성에 실패했습니다. AI 서비스 연결 상태를 확인하고 다시 시도해 주세요.",
  },
  {
    pattern: /Word 파일|docx|python.?docx/i,
    message: "Word 파일 생성에 실패했습니다. 다시 시도해 주세요.",
  },
  {
    pattern: /한글 PDF 생성에 필요한 폰트|AppleGothic|맑은 고딕.*설치/i,
    message: "한글 PDF 생성에 필요한 폰트를 찾을 수 없습니다. Word(.docx) 형식을 사용해 주세요.",
  },
  {
    pattern: /PDF 파일|reportlab/i,
    message: "PDF 파일 생성에 실패했습니다. 다시 시도해 주세요.",
  },
  {
    pattern: /파일 저장 실패|file.*save.*fail/i,
    message: "파일 저장에 실패했습니다. 다운로드 폴더 접근 권한을 확인해 주세요.",
  },
];

/**
 * Convert a raw technical error value to a user-friendly Korean message.
 *
 * @param {unknown} error - The caught error (Error object, string, etc.)
 * @param {string} [fallback] - Optional custom fallback message
 * @returns {string} Korean user-friendly error message
 */
export function toUserMessage(error, fallback) {
  const raw =
    error instanceof Error
      ? error.message
      : typeof error === "string"
      ? error
      : String(error);

  for (const { pattern, message } of ERROR_MAPPINGS) {
    if (pattern.test(raw)) {
      return message;
    }
  }

  return fallback ?? "오류가 발생했습니다. 다시 시도해 주세요.";
}
