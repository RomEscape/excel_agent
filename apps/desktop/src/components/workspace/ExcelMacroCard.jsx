/**
 * 매크로 카드 — 계획 미리보기 · 진행률 · 실패.
 *
 * 상태는 excelMacroStore가 갖고 있고 진행은 excelMacroManager가 한다. 이 컴포넌트는
 * 구독해서 그리기만 한다.
 */

import { AlertTriangle, Check, ListChecks, RefreshCw, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  abortMacro,
  cancelMacroPlan,
  continueMacroSkippingCurrent,
  dismissMacro,
  runMacro,
} from "@/lib/excelMacroManager";
import { cn } from "@/lib/utils";
import useExcelMacroStore from "@/store/excelMacroStore";

/** 단계 상태별 표시 */
const STEP_MARK = {
  done: { icon: "✓", className: "text-emerald-600 dark:text-emerald-400" },
  failed: { icon: "✕", className: "text-red-600 dark:text-red-400" },
  skipped: { icon: "—", className: "text-muted-foreground" },
  pending: { icon: "·", className: "text-muted-foreground" },
};

function StepRow({ step, checked, onToggle, highlight }) {
  const mark = STEP_MARK[step.status] || STEP_MARK.pending;
  return (
    <li
      className={cn(
        "flex items-start gap-1.5 rounded px-1.5 py-1",
        highlight && "bg-primary/10",
      )}
    >
      {onToggle ? (
        <input
          type="checkbox"
          checked={checked}
          onChange={() => onToggle(step.index)}
          className="mt-0.5 h-3 w-3 shrink-0"
          aria-label={`${step.index}단계 실행 여부`}
        />
      ) : (
        <span className={cn("w-3 shrink-0 text-center", mark.className)}>{mark.icon}</span>
      )}
      <span className="w-5 shrink-0 text-right text-muted-foreground">{step.index}.</span>
      <span className={cn("flex-1 break-words", !checked && onToggle && "line-through opacity-50")}>
        {step.command}
        {step.destructive && (
          <span className="ml-1 inline-flex items-center gap-0.5 rounded bg-amber-100 px-1 text-[10px] text-amber-700 dark:bg-amber-950 dark:text-amber-300">
            <AlertTriangle className="h-2.5 w-2.5" />
            데이터 삭제
          </span>
        )}
        {step.warnings.map((warning) => (
          <span key={warning} className="ml-1 text-[10px] text-amber-600 dark:text-amber-400">
            {warning}
          </span>
        ))}
        {step.detail && step.status === "failed" && (
          <span className="ml-1 text-[10px] text-red-600 dark:text-red-400">{step.detail}</span>
        )}
      </span>
    </li>
  );
}

export default function ExcelMacroCard() {
  const status = useExcelMacroStore((s) => s.status);
  const steps = useExcelMacroStore((s) => s.steps);
  const total = useExcelMacroStore((s) => s.total);
  const completed = useExcelMacroStore((s) => s.completed);
  const cursor = useExcelMacroStore((s) => s.cursor);
  const skipIndices = useExcelMacroStore((s) => s.skipIndices);
  const followUpQuestion = useExcelMacroStore((s) => s.followUpQuestion);
  const failureReason = useExcelMacroStore((s) => s.failureReason);
  const backupPath = useExcelMacroStore((s) => s.backupPath);
  const busy = useExcelMacroStore((s) => s.busy);
  const toggleSkip = useExcelMacroStore((s) => s.toggleSkip);

  if (status === "idle" || steps.length === 0) return null;

  const running = status === "running";
  const selectedCount = steps.length - skipIndices.length;

  return (
    <div className="rounded-lg border border-border bg-background p-2.5 text-xs">
      <div className="mb-1.5 flex items-center gap-1.5 font-medium">
        <ListChecks className="h-3.5 w-3.5 text-primary" />
        {status === "planned" && `${steps.length}개 작업으로 나눴습니다`}
        {running && (
          <>
            <RefreshCw className="h-3 w-3 animate-spin text-primary" />
            {`${cursor}/${total} 실행 중`}
          </>
        )}
        {status === "waiting_input" && `${cursor}/${total} — 추가 정보가 필요합니다`}
        {status === "halted" && `${cursor}/${total} 단계에서 멈췄습니다`}
        {status === "done" && `${completed}개 작업을 마쳤습니다`}
        {status === "aborted" && "중단했습니다"}
      </div>

      <ul className="max-h-52 space-y-0.5 overflow-y-auto">
        {steps.map((step) => (
          <StepRow
            key={step.index}
            step={step}
            checked={!skipIndices.includes(step.index)}
            onToggle={status === "planned" ? toggleSkip : null}
            highlight={status !== "planned" && step.index === cursor}
          />
        ))}
      </ul>

      {status === "waiting_input" && followUpQuestion && (
        <p className="mt-1.5 rounded bg-muted/50 px-2 py-1.5 text-[11px]">
          {followUpQuestion}
          <span className="mt-0.5 block text-muted-foreground">
            아래 입력창에 답하면 이어서 진행합니다.
          </span>
        </p>
      )}

      {status === "halted" && failureReason && (
        <p className="mt-1.5 rounded bg-red-50 px-2 py-1.5 text-[11px] text-red-700 dark:bg-red-950 dark:text-red-300">
          {failureReason}
        </p>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {status === "planned" && (
          <>
            <Button size="sm" className="h-6 px-2 text-[11px]" disabled={busy || selectedCount === 0} onClick={() => runMacro()}>
              <Check className="mr-1 h-3 w-3" />
              {selectedCount}개 실행
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2 text-[11px]"
              disabled={busy}
              onClick={() => cancelMacroPlan()}
            >
              취소
            </Button>
          </>
        )}

        {(status === "waiting_input" || status === "halted") && (
          <>
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2 text-[11px]"
              disabled={busy}
              onClick={() => continueMacroSkippingCurrent()}
            >
              이어서 하기
            </Button>
            {backupPath && (
              <Button
                size="sm"
                variant="outline"
                className="h-6 px-2 text-[11px]"
                disabled={busy}
                onClick={() => abortMacro({ rollback: true })}
              >
                되돌리기
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[11px]"
              disabled={busy}
              onClick={() => abortMacro({ rollback: false })}
            >
              여기서 멈추기
            </Button>
          </>
        )}

        {(status === "done" || status === "aborted") && (
          <Button size="sm" variant="ghost" className="h-6 px-2 text-[11px]" onClick={dismissMacro}>
            <X className="mr-1 h-3 w-3" />
            닫기
          </Button>
        )}
      </div>
    </div>
  );
}
