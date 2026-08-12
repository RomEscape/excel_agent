/**
 * 온보딩 위저드 UI primitive — 최종 와이어프레임 A군(Frame 159~165 / 145~151).
 *
 * 세 화면이 공유하는 조각들이다:
 *   WizardSteps    3단계 인디케이터 (파일 설치 → 모델 설치 → 워크스페이스 지정)
 *   InstallProgress 진행 바 467×12 + 좌우 라벨
 *   FileChecklist   설치 중인 파일 체크리스트
 *   ModelSelectField 제조사 아이콘 + 모델명 + `추천` 배지 + 드롭다운
 *   FolderField     폴더 아이콘 + 선택된 경로 (없으면 placeholder)
 *   WizardCta       120×36 확인 버튼 (비활성이면 지면색)
 *
 * 상태를 갖지 않는다 — 모델 표시 규칙은 lib/modelCatalog.js가 소유한다.
 */
import * as React from "react";
import { Check, ChevronDown, FileText, Folder, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/** 와이어프레임의 3단계. 모든 온보딩 화면 하단에 공통으로 붙는다. */
export const WIZARD_STEPS = Object.freeze(["파일 설치", "모델 설치", "워크스페이스 지정"]);

/**
 * 3단계 인디케이터.
 *
 * 활성 스텝은 #56C331(--brand-step) 채움 + 흰 숫자, 비활성은 지면 + 초록 숫자.
 * 지나간 스텝은 체크로 바꾼다 — 와이어프레임에는 없지만, 숫자만 있으면
 * "지나간 것"과 "아직 안 온 것"이 똑같이 비활성으로 보인다.
 *
 * @param {number} current 0-based 활성 스텝
 */
export function WizardSteps({ current }) {
  return (
    <ol className="flex items-center justify-center gap-6" aria-label="설치 진행 단계">
      {WIZARD_STEPS.map((label, i) => {
        const active = i === current;
        const done = i < current;
        return (
          <li key={label} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className={cn(
                "flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold",
                active || done
                  ? "bg-brand-step text-white"
                  : "border border-brand-step/40 bg-background text-brand-step"
              )}
            >
              {done ? <Check className="h-2.5 w-2.5" /> : i + 1}
            </span>
            <span
              className={cn(
                "text-[11px]",
                active ? "font-semibold text-foreground" : "text-muted-foreground"
              )}
              aria-current={active ? "step" : undefined}
            >
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * 설치 진행 바 — 와이어프레임 Frame 98 (467×12 트랙 + 하단 좌우 라벨).
 *
 * @param {number} value 0~100
 * @param {string} [label] 좌측 라벨 (`현재 파일명 abcdefg..`)
 * @param {string} [detail] 우측 라벨 (`1734/10200(17%)`)
 */
export function InstallProgress({ value = 0, label, detail }) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    <div className="flex w-full max-w-[29.2rem] flex-col gap-2">
      <div
        className="h-3 w-full overflow-hidden rounded-full bg-border"
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      {(label || detail) && (
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <span className="min-w-0 truncate">{label}</span>
          <span className="shrink-0">{detail}</span>
        </div>
      )}
    </div>
  );
}

/**
 * 설치 파일 체크리스트 — 와이어프레임 Group 1.
 * 완료는 초록 체크(#46C642), 진행 중은 스피너, 대기는 빈 원.
 *
 * @param {Array<{name: string, state: 'done'|'active'|'pending'}>} items
 */
export function FileChecklist({ items }) {
  if (!Array.isArray(items) || items.length === 0) return null;
  return (
    <ul className="flex flex-col gap-2">
      {items.map((item) => (
        <li
          key={item.name}
          className="flex items-center gap-2.5 rounded-lg border border-border bg-card px-3 py-2"
        >
          <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate text-xs text-foreground">{item.name}</span>
          {item.state === "done" ? (
            <Check className="h-3.5 w-3.5 shrink-0 text-brand-file" />
          ) : item.state === "active" ? (
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
          ) : (
            <span
              className="h-3.5 w-3.5 shrink-0 rounded-full border border-border"
              aria-hidden="true"
            />
          )}
        </li>
      ))}
    </ul>
  );
}

/** `추천` 배지 — 와이어프레임 Frame 111. */
function RecommendBadge() {
  return (
    <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold text-primary">
      추천
    </span>
  );
}

/** 제조사 색 점 — 와이어프레임의 제조사 아이콘 자리. */
function BrandDot({ color }) {
  return (
    <span
      className="h-3.5 w-3.5 shrink-0 rounded-full"
      style={{ backgroundColor: color }}
      aria-hidden="true"
    />
  );
}

/**
 * 모델 셀렉트 — 와이어프레임 A-3(닫힘) / A-4(열림), 441×40.
 *
 * 네이티브 <select>를 안 쓰는 이유: 항목마다 제조사 색 점과 `추천` 배지가
 * 들어가는데 <option>에는 마크업을 못 넣는다.
 *
 * @param {Array<ReturnType<import('@/lib/modelCatalog').describeModel>>} options
 * @param {string} value 선택된 모델 ID
 * @param {(id: string) => void} onChange
 */
export function ModelSelectField({ options, value, onChange, disabled, placeholder = "모델을 선택해주세요." }) {
  const [open, setOpen] = React.useState(false);
  const rootRef = React.useRef(null);

  // 바깥 클릭·Esc로 닫는다. 안 닫으면 목록이 뜬 채로 다음 단계로 넘어간다.
  React.useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (!rootRef.current?.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const selected = options.find((o) => o.id === value);

  return (
    <div ref={rootRef} className="relative w-full max-w-[27.5rem]">
      <button
        type="button"
        onClick={() => !disabled && setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cn(
          "flex h-10 w-full items-center gap-2 rounded-lg border bg-card px-3 text-left transition-colors",
          open ? "border-primary" : "border-border",
          disabled && "opacity-50"
        )}
      >
        {selected ? (
          <>
            <BrandDot color={selected.color} />
            <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
              {selected.name}
              {selected.tag && (
                <span className="ml-1 text-muted-foreground">{selected.tag}</span>
              )}
            </span>
            {selected.recommended && <RecommendBadge />}
          </>
        ) : (
          <span className="flex-1 text-xs text-muted-foreground">{placeholder}</span>
        )}
        <ChevronDown
          className={cn(
            "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180"
          )}
        />
      </button>

      {open && (
        <ul
          role="listbox"
          className="absolute left-0 right-0 top-full z-20 mt-1 max-h-52 overflow-y-auto rounded-lg border border-border bg-popover py-1 shadow-lg"
        >
          {options.length === 0 ? (
            <li className="px-3 py-2 text-xs text-muted-foreground">
              설치된 모델이 없습니다.
            </li>
          ) : (
            options.map((opt) => (
              <li key={opt.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={opt.id === value}
                  onClick={() => {
                    onChange?.(opt.id);
                    setOpen(false);
                  }}
                  className={cn(
                    "flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-accent",
                    opt.id === value && "bg-accent"
                  )}
                >
                  <BrandDot color={opt.color} />
                  <span className="min-w-0 flex-1 truncate text-xs text-foreground">
                    {opt.name}
                    {opt.tag && <span className="ml-1 text-muted-foreground">{opt.tag}</span>}
                  </span>
                  {opt.recommended && <RecommendBadge />}
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}

/**
 * 폴더 선택 필드 — 와이어프레임 A-6(미선택) / A-7(선택됨), 441×40.
 * 값이 없으면 placeholder, 있으면 경로. 폴더 아이콘은 #46C642(--brand-file).
 */
export function FolderField({ value, placeholder = "폴더를 선택해주세요.", onClick, disabled }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex h-10 w-full max-w-[27.5rem] items-center gap-2.5 rounded-lg border border-border bg-card px-3 text-left transition-colors hover:border-primary/50",
        disabled && "opacity-50"
      )}
    >
      <Folder className="h-4 w-4 shrink-0 text-brand-file" />
      <span
        className={cn(
          "min-w-0 flex-1 truncate text-xs",
          value ? "text-foreground" : "text-muted-foreground"
        )}
        title={value || placeholder}
      >
        {value || placeholder}
      </span>
    </button>
  );
}

/**
 * 확인 CTA — 와이어프레임 Frame 71 (120×36).
 * 활성은 브랜드 초록, 비활성은 #E1E6DF(--border) 지면.
 */
export function WizardCta({ children = "확인", onClick, disabled, busy }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      className={cn(
        "flex h-9 w-[7.5rem] items-center justify-center rounded-lg text-xs font-semibold transition-colors",
        disabled || busy
          ? "cursor-not-allowed bg-border text-muted-foreground"
          : "bg-primary text-primary-foreground hover:bg-primary/90"
      )}
    >
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : children}
    </button>
  );
}

/** 위저드 화면의 공통 껍데기 — 타이틀 + 본문 + 하단 스텝 인디케이터. */
export function WizardScreen({ title, help, step, children, footer }) {
  return (
    <div className="flex min-h-full flex-col items-center justify-center gap-8 px-8 py-10">
      <div className="flex w-full flex-col items-center gap-6">
        {title && (
          <h2 className="text-center text-xl font-bold text-foreground">{title}</h2>
        )}
        <div className="flex w-full flex-col items-center gap-4">{children}</div>
        {help && <p className="text-center text-xs text-muted-foreground">{help}</p>}
        {footer}
      </div>
      {typeof step === "number" && <WizardSteps current={step} />}
    </div>
  );
}
