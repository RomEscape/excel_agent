/**
 * 채팅 표면의 공용 UI primitive.
 *
 * 목업(desktop-app)의 2·3번 화면에서 뽑은 조각들 — 아바타 붙은 좌측 어시스턴트
 * 버블, 우측 사용자 버블, 첨부 칩, 빠른 프롬프트 칩 행, 둥근 컴포저.
 * 여기 있는 것들은 상태를 갖지 않는다. 도메인 로직은 lib/chatManager.js가,
 * 조합은 components/chat/ChatPage.jsx가 맡는다.
 *
 * 목업의 lime 계열 색은 브랜드 초록의 Figma 근사치이므로 전부 primary 토큰으로
 * 옮겼다 (CLAUDE.md 브랜드 색 노트 — 코드에서 새 브랜드 색을 짓지 않는다).
 */
import * as React from "react";
import {
  AlertCircle,
  ArrowUp,
  Loader2,
  Paperclip,
  Shield,
  X,
} from "lucide-react";
import { BrandMark } from "@/components/ui/logo";
import { cn } from "@/lib/utils";

/**
 * 어시스턴트 아바타.
 *
 * 목업은 속이 빈 초록 원이지만, BrandMark가 이미 자기 배경(라운드 타일)을 가진
 * 앱 아이콘이라 초록 원 안에 넣으면 배경이 두 겹이 된다. 마크 자체를 원으로
 * 잘라 쓰는 편이 브랜드도 드러나고 겹침도 없다.
 */
export function AgentAvatar({ className }) {
  return (
    <span
      className={cn("block h-9 w-9 shrink-0 overflow-hidden rounded-full", className)}
      aria-hidden="true"
    >
      <BrandMark className="h-full w-full object-cover" />
    </span>
  );
}

/** 어시스턴트 이름표 — 목업의 "김대리 AI [로컬]". */
export function AgentLabel({ children = "김대리 AI" }) {
  return (
    <span className="text-xs font-bold text-muted-foreground">
      {children} <span className="font-normal">[로컬]</span>
    </span>
  );
}

/**
 * 메시지 버블.
 *
 * 목업은 버블에 고정 폭(w-[532px])을 박아놨지만 그대로 옮기면 창을 줄였을 때
 * 잘린다. 최대 폭 비율로 바꾸고 내용에 따라 늘어나게 했다.
 *
 * @param {'user'|'agent'|'system'} role
 * @param {React.ReactNode} children 본문
 * @param {React.ReactNode} [footer] 버블 아래 부가 정보 (도구 실행 수 등)
 * @param {React.ReactNode} [attachment] 버블 아래 결과 카드
 */
export function MessageBubble({ role, children, footer, attachment }) {
  // 시스템 메시지는 가운데 정렬된 알약 — 대화 흐름 밖의 이벤트임을 드러낸다.
  if (role === "system") {
    return (
      <div className="flex justify-center">
        <span className="rounded-full bg-muted px-3 py-1 text-[11px] text-muted-foreground">
          {children}
        </span>
      </div>
    );
  }

  if (role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%] whitespace-pre-wrap break-words rounded-xl bg-primary px-4 py-3 text-xs leading-relaxed text-primary-foreground">
          {children}
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-3">
      <AgentAvatar />
      <div className="flex min-w-0 flex-1 flex-col items-start gap-1.5">
        <AgentLabel />
        <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-xl bg-muted px-4 py-3 text-xs leading-relaxed text-foreground">
          {children}
        </div>
        {attachment && <div className="w-full max-w-[85%]">{attachment}</div>}
        {footer}
      </div>
    </div>
  );
}

/** 오류 버블 — 아바타는 유지하되 destructive 톤으로 구분한다. */
export function ErrorBubble({ children }) {
  return (
    <div className="flex items-start gap-3">
      <AgentAvatar />
      <div className="flex min-w-0 flex-col items-start gap-1.5">
        <AgentLabel />
        <div className="flex max-w-[85%] items-start gap-1.5 rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-xs leading-relaxed text-destructive">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="break-words">{children}</span>
        </div>
      </div>
    </div>
  );
}

/** 처리 중 표시 — 목업의 "AI 분석 중 ●●●" 자리. */
export function TypingBubble({ label }) {
  return (
    <div className="flex items-start gap-3">
      <AgentAvatar />
      <div className="flex flex-col items-start gap-1.5">
        <AgentLabel />
        <span className="inline-flex items-center gap-2 rounded-xl bg-muted px-4 py-3 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
          {label || "요청을 처리하고 있습니다..."}
        </span>
      </div>
    </div>
  );
}

/** 첨부 파일 칩 — 목업 3번의 "📄 8월_매출데이터_원본.xlsx ✕". */
export function AttachmentChip({ name, onRemove }) {
  return (
    <span className="inline-flex max-w-full items-center gap-2 rounded-lg bg-muted px-3.5 py-2 text-xs font-semibold text-foreground">
      <span className="truncate" title={name}>
        {name}
      </span>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="shrink-0 text-muted-foreground transition-colors hover:text-foreground"
          aria-label={`${name} 첨부 해제`}
        >
          <X className="h-3 w-3" />
        </button>
      )}
    </span>
  );
}

/**
 * 빠른 프롬프트 칩 행 — 목업 3번의 "✨ 엑셀 맞춤형 빠른 프롬프트 추천".
 *
 * @param {Array<{label: string, prompt: string}>} prompts
 * @param {(prompt: string) => void} onPick
 */
export function QuickPromptRow({ title = "빠른 프롬프트 추천", prompts, onPick, disabled }) {
  if (!Array.isArray(prompts) || prompts.length === 0) return null;
  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-xs font-bold text-muted-foreground">{title}</p>
      <div className="flex flex-wrap gap-2">
        {prompts.map((p) => (
          <button
            key={p.label}
            type="button"
            disabled={disabled}
            onClick={() => onPick?.(p.prompt)}
            title="클릭하면 입력창에 채워집니다"
            className="rounded-[20px] border border-border bg-background px-3.5 py-2 text-xs font-semibold text-foreground transition-colors hover:border-primary/50 hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * 컴포저 — 목업의 둥근 입력 박스 (📎 + textarea + 원형 전송 버튼).
 *
 * IME 조합 중 Enter는 한글 변환 확정이므로 전송하면 안 된다. 이 가드는
 * 컴포저가 소유한다 — 쓰는 쪽마다 다시 짜면 반드시 한 곳에서 빠진다.
 *
 * @param {object} props
 * @param {string} props.value
 * @param {(v: string) => void} props.onChange
 * @param {() => void} props.onSubmit
 * @param {() => void} [props.onAttach] 없으면 클립 버튼을 숨긴다
 */
export const ChatComposer = React.forwardRef(function ChatComposer(
  {
    value,
    onChange,
    onSubmit,
    onAttach,
    disabled = false,
    busy = false,
    placeholder = "김대리에게 업무를 지시하세요...",
    footnote,
    header,
    focused = false,
  },
  ref
) {
  const innerRef = React.useRef(null);
  const textareaRef = ref || innerRef;

  // auto-grow: 1행 ~ 6행. 매 렌더 height를 auto로 되돌린 뒤 scrollHeight를 재야
  // 글자를 지웠을 때도 다시 줄어든다.
  React.useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 140)}px`;
  }, [value, textareaRef]);

  const handleKeyDown = (e) => {
    // 한글/일본어/중국어 조합 중 Enter는 변환 확정 — 전송 금지.
    if (e.nativeEvent?.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSubmit?.();
    }
  };

  const canSend = !disabled && !busy && String(value || "").trim().length > 0;

  return (
    <div className="flex flex-col gap-2">
      {header}
      <div
        className={cn(
          "flex items-end gap-2.5 rounded-2xl border bg-background px-4 py-3 transition-colors",
          focused ? "border-primary" : "border-border"
        )}
      >
        {onAttach && (
          <button
            type="button"
            onClick={onAttach}
            disabled={disabled}
            className="shrink-0 pb-1 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40"
            aria-label="파일 첨부"
            title="파일 첨부"
          >
            <Paperclip className="h-4 w-4" />
          </button>
        )}
        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          onChange={(e) => onChange?.(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={placeholder}
          className="max-h-[140px] flex-1 resize-none bg-transparent text-xs leading-relaxed text-foreground placeholder:text-muted-foreground focus:outline-none disabled:opacity-50"
        />
        <button
          type="button"
          onClick={() => onSubmit?.()}
          disabled={!canSend}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground transition-opacity hover:bg-primary/90 disabled:opacity-40"
          aria-label="전송"
        >
          {busy ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <ArrowUp className="h-4 w-4" />
          )}
        </button>
      </div>
      {footnote && (
        <p className="flex items-center justify-center gap-1.5 text-center text-xs text-muted-foreground">
          <Shield className="h-3 w-3" />
          {footnote}
        </p>
      )}
    </div>
  );
});

/** 컴포저 아래 기본 안내 문구 — 목업의 보안 각주. */
export const LOCAL_ONLY_FOOTNOTE =
  "모든 데이터는 당신의 컴퓨터 안에서만 안전하게 처리됩니다.";
