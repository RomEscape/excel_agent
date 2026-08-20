/**
 * 채팅 표면의 공용 UI primitive.
 *
 * 최종 와이어프레임(design/desktop-shell/SCREENS.md B-5 ~ B-7)의 조각들이다.
 * 여기 있는 것들은 상태를 갖지 않는다 — 도메인 로직은 lib/chatManager.js가,
 * 조합은 components/chat/ChatPanel.jsx가 맡는다.
 *
 * 구버전과 달라진 점:
 *   - 말풍선 아래 액션 행(되돌리기·편집·복사)과 타임스탬프가 붙었다.
 *   - AI 말풍선이 아바타+이름표 딸린 좌측 정렬에서 스레드 폭을 꽉 채우는
 *     지면(--chat-bubble)으로 바뀌었다. 패널 폭이 390px뿐이라 아바타에
 *     가로를 내주면 본문이 두 배로 접힌다.
 *   - CONFIRM 승인이 모달이 아니라 말풍선 인라인 버튼(`네 Y` / `아니오 N`)이다.
 *   - 툴 진행 스텝 칩 · 스켈레톤 로딩이 새로 생겼다.
 *
 * 색은 전부 테마 토큰이다 (CLAUDE.md 브랜드 색 노트 — 코드에서 새 브랜드 색을
 * 짓지 않는다). 사용자 말풍선이 --brand(#2DB400)가 아니라 --primary인 이유는
 * index.css 주석 참조: #2DB400 위의 흰 글자는 라이트에서 2.75:1이라 본문 대비에 못 미친다.
 */
import * as React from "react";
import {
  AlertCircle,
  ArrowUp,
  Check,
  Copy,
  Loader2,
  Paperclip,
  Pencil,
  Undo2,
} from "lucide-react";
import { cn } from "@/lib/utils";

/** 스레드 최대 폭 — 와이어프레임 358px (패널 390 - 좌우 16씩). */
const THREAD_WIDTH = "w-full";

/**
 * 타임스탬프 — 와이어프레임의 `오후 8:00`.
 * Date 객체나 ISO 문자열을 받고, 없으면 아무것도 그리지 않는다.
 */
export function formatBubbleTime(value) {
  if (!value) return "";
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" });
}

/**
 * 말풍선 아래 액션 아이콘 한 개.
 * 복사는 눌린 걸 알려줘야 해서 1.5초간 체크로 바뀐다.
 */
function BubbleAction({ icon: Icon, label, onClick, done }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
    >
      {done ? <Check className="h-4 w-4 text-primary" /> : <Icon className="h-4 w-4" />}
    </button>
  );
}

/**
 * 말풍선 액션 행 — 와이어프레임 Frame 135 / Frame 137.
 *
 * 사용자: 되돌리기 · 편집 · 복사   (Frame 134, 64×16 = 아이콘 3개)
 * AI    : 되돌리기 · 복사          (Frame 136, 36×16 = 아이콘 2개)
 *
 * `되돌리기`는 그 메시지 이후를 잘라내고 다시 보내는 동작이라 파괴적이다.
 * 핸들러를 안 넘기면 버튼 자체를 그리지 않는다 — 눌러도 아무 일 없는 아이콘은
 * 고장으로 읽힌다.
 */
export function BubbleActions({ text, onRetry, onEdit, align = "start", children }) {
  const [copied, setCopied] = React.useState(false);
  const timerRef = React.useRef(null);

  React.useEffect(() => () => timerRef.current && clearTimeout(timerRef.current), []);

  const handleCopy = React.useCallback(() => {
    const value = String(text ?? "");
    if (!value) return;
    navigator.clipboard?.writeText(value).then(
      () => {
        setCopied(true);
        if (timerRef.current) clearTimeout(timerRef.current);
        timerRef.current = setTimeout(() => setCopied(false), 1500);
      },
      () => {}
    );
  }, [text]);

  return (
    <div
      className={cn(
        "flex items-center gap-1",
        align === "end" ? "justify-end" : "justify-start"
      )}
    >
      {onRetry && <BubbleAction icon={Undo2} label="이 지점부터 다시 시도" onClick={onRetry} />}
      {onEdit && <BubbleAction icon={Pencil} label="메시지 편집" onClick={onEdit} />}
      <BubbleAction icon={Copy} label="복사" onClick={handleCopy} done={copied} />
      {children}
    </div>
  );
}

/**
 * 메시지 말풍선.
 *
 * @param {'user'|'agent'|'system'} role
 * @param {React.ReactNode} children 본문
 * @param {string} [time] 타임스탬프 (ISO 문자열 또는 Date)
 * @param {React.ReactNode} [actions] 액션 행 (BubbleActions)
 * @param {React.ReactNode} [footer] 버블 아래 부가 정보 (마스킹 안내 등)
 */
export function MessageBubble({ role, children, time, actions, footer }) {
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

  const isUser = role === "user";
  const stamp = formatBubbleTime(time);

  return (
    <div className={cn("flex flex-col gap-1", THREAD_WIDTH)}>
      <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
        <div
          className={cn(
            "whitespace-pre-wrap break-words rounded-xl px-3.5 py-2.5 text-xs leading-relaxed",
            isUser
              ? "max-w-[80%] bg-primary text-primary-foreground"
              : "w-full bg-chat-bubble text-chat-bubble-foreground"
          )}
        >
          {children}
        </div>
      </div>
      {stamp && (
        <span
          className={cn(
            "text-[11px] text-muted-foreground",
            isUser ? "text-right" : "text-left"
          )}
        >
          {stamp}
        </span>
      )}
      {actions}
      {footer}
    </div>
  );
}

/** 오류 버블 — destructive 톤으로 구분한다. */
export function ErrorBubble({ children }) {
  return (
    <div className="flex w-full items-start gap-1.5 rounded-xl border border-destructive/40 bg-destructive/10 px-3.5 py-2.5 text-xs leading-relaxed text-destructive">
      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span className="break-words">{children}</span>
    </div>
  );
}

/**
 * 인라인 CONFIRM 승인 — 와이어프레임 B-6의 `네 Y` / `아니오 N` (각 56×21).
 *
 * 모달이 아니라 말풍선 옆에 붙는다. 모달은 스레드를 가려서 "무엇에 대한
 * 승인인지"를 다시 확인할 수 없게 만드는데, 승인 대상 문장이 바로 위 말풍선에
 * 있는 구조라 인라인이 맞다.
 *
 * `Y` / `N` 단축키 힌트는 라벨에 그대로 있다 — 실제 키 처리는 패널이 한다.
 */
export function InlineApproval({ onApprove, onReject, busy }) {
  return (
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        onClick={onApprove}
        disabled={busy}
        className="rounded-md border border-primary/40 bg-background px-2.5 py-1 text-[11px] font-semibold text-primary transition-colors hover:bg-primary hover:text-primary-foreground disabled:opacity-50"
      >
        {busy ? "처리 중..." : "네 Y"}
      </button>
      <button
        type="button"
        onClick={onReject}
        disabled={busy}
        className="rounded-md border border-border bg-background px-2.5 py-1 text-[11px] font-semibold text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
      >
        아니오 N
      </button>
    </div>
  );
}

/**
 * 툴 진행 스텝 칩 — 와이어프레임 B-7의 `문서 형식 파악 완료` → `데이터 처리 완료`.
 * 358×28, 지면은 AI 말풍선과 같은 --chat-bubble.
 */
export function ToolStepChip({ label, done = true }) {
  return (
    <div className="flex w-full items-center gap-2 rounded-md bg-chat-bubble px-3 py-1.5 text-[11px] text-chat-bubble-foreground">
      {done ? (
        <Check className="h-3 w-3 shrink-0 text-primary" />
      ) : (
        <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />
      )}
      <span className="truncate">{label}</span>
    </div>
  );
}

/**
 * 스켈레톤 로딩 — 와이어프레임 B-7의 `새로운 시트 제작중...` + shimmer 바.
 *
 * 구버전 TypingBubble(말풍선 안 스피너)을 대신한다. 진행 중인 작업이
 * "무언가를 만들고 있다"는 걸 바 모양으로 드러내는 쪽이 스펙이다.
 */
export function ThinkingSkeleton({ label }) {
  return (
    <div className="flex w-full flex-col gap-2">
      <div className="flex items-center gap-2">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
        <span className="text-xs text-muted-foreground">
          {label || "요청을 처리하고 있습니다..."}
        </span>
      </div>
      <div className="oc-shimmer h-12 w-full rounded-lg bg-card" />
    </div>
  );
}

/** 첨부 파일 칩 — 입력창에 붙은 엑셀 범위 참조 등. */
export function AttachmentChip({ name, onRemove }) {
  return (
    <span className="inline-flex max-w-full items-center gap-2 rounded-lg bg-muted px-3 py-1.5 text-[11px] font-semibold text-foreground">
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
          ✕
        </button>
      )}
    </span>
  );
}

/**
 * 빠른 프롬프트 칩 행 — 와이어프레임 B-2의 358×32 칩 3개 + chevron-right 더보기.
 *
 * 가로 스크롤이다 (패널이 390px라 3개가 한 줄에 안 들어간다). 더보기 chevron은
 * 스크롤 가능함을 알리는 힌트 겸 다음 칩으로 밀어주는 버튼이다.
 *
 * @param {Array<{label: string, prompt: string}>} prompts
 * @param {(prompt: string) => void} onPick
 */
export function QuickPromptRow({ prompts, onPick, disabled }) {
  const scrollRef = React.useRef(null);

  if (!Array.isArray(prompts) || prompts.length === 0) return null;

  const scrollNext = () => {
    scrollRef.current?.scrollBy({ left: 140, behavior: "smooth" });
  };

  return (
    <div className="flex items-center gap-1">
      <div
        ref={scrollRef}
        className="flex min-w-0 flex-1 gap-2 overflow-x-auto scrollbar-none"
        style={{ scrollbarWidth: "none" }}
      >
        {prompts.map((p) => (
          <button
            key={p.label}
            type="button"
            disabled={disabled}
            onClick={() => onPick?.(p.prompt)}
            title={p.prompt}
            className="shrink-0 whitespace-nowrap rounded-lg border border-border bg-background px-3 py-1.5 text-[11px] font-medium text-foreground transition-colors hover:border-primary/50 hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
          >
            {p.label}
          </button>
        ))}
      </div>
      <button
        type="button"
        onClick={scrollNext}
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        aria-label="빠른 프롬프트 더 보기"
      >
        ›
      </button>
    </div>
  );
}

/**
 * 컴포저 — 와이어프레임 Frame 130 (390×143).
 *
 * placeholder + 하단 액션 행(paperclip 왼쪽 · arrow-up 오른쪽) 구성이다.
 *
 * IME 조합 중 Enter는 한글 변환 확정이므로 전송하면 안 된다. 이 가드는
 * 컴포저가 소유한다 — 쓰는 쪽마다 다시 짜면 반드시 한 곳에서 빠진다.
 */
export const ChatComposer = React.forwardRef(function ChatComposer(
  {
    value,
    onChange,
    onSubmit,
    onAttach,
    disabled = false,
    busy = false,
    placeholder = "김대리에게 명령을 내려주세요.",
    header,
    focused = false,
    minHeight = 72,
  },
  ref
) {
  const innerRef = React.useRef(null);
  const textareaRef = ref || innerRef;

  // auto-grow. 매 렌더 height를 auto로 되돌린 뒤 scrollHeight를 재야
  // 글자를 지웠을 때도 다시 줄어든다.
  React.useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(Math.max(ta.scrollHeight, minHeight), 160)}px`;
  }, [value, textareaRef, minHeight]);

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
          "flex flex-col gap-1 rounded-xl border bg-card px-3 py-2.5 transition-colors",
          focused ? "border-primary" : "border-border"
        )}
      >
        <textarea
          ref={textareaRef}
          rows={2}
          value={value}
          onChange={(e) => onChange?.(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={placeholder}
          className="max-h-[160px] w-full resize-none bg-transparent text-xs leading-relaxed text-foreground placeholder:text-muted-foreground focus:outline-none disabled:opacity-50"
        />
        <div className="flex items-center justify-between">
          {onAttach ? (
            <button
              type="button"
              onClick={onAttach}
              disabled={disabled}
              className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"
              aria-label="파일 첨부"
              title="파일 첨부"
            >
              <Paperclip className="h-4 w-4" />
            </button>
          ) : (
            <span />
          )}
          <button
            type="button"
            onClick={() => onSubmit?.()}
            disabled={!canSend}
            className="flex h-7 w-7 items-center justify-center rounded-full bg-primary text-primary-foreground transition-opacity hover:bg-primary/90 disabled:opacity-40"
            aria-label="전송"
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <ArrowUp className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
      </div>
    </div>
  );
});

/** 컴포저 아래 기본 안내 문구 — 홈 화면에서만 쓴다. */
export const LOCAL_ONLY_FOOTNOTE =
  "모든 데이터는 당신의 컴퓨터 안에서만 안전하게 처리됩니다.";
