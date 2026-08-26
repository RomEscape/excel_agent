/**
 * Ollama 모델 선택기 — 자유 입력 대신 *실제로 설치된* 모델만 고르게 한다.
 *
 * 데이터 소스: 중앙 `statusStore.modules.ollama` — App의 useStatusPoller가 자동 갱신.
 * 액션:        `STATUS_MODULES.ollama.check()` (수동 새로고침)
 *
 * 상태별 UI:
 *   - 미설치(`installed=false`)        → 안내 + "재진단" 버튼
 *   - 데몬 미실행(`running=false`)     → 안내 + "재진단" 버튼
 *   - 모델 0개(`models=[]`)            → 안내 (모델 받는 법) + "재진단" 버튼
 *   - 정상                             → Select drop + 새로고침 버튼
 *
 * 현재 저장된 model이 설치 목록에 없으면 (예: 사용자가 이전에 받았다가 지운 경우)
 * Select의 별도 항목으로 노출하되 "현재 설치되지 않음" 경고 라벨을 붙인다.
 */
import React, { useState } from "react";
import { Loader2, RefreshCw, AlertTriangle, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import useStatusStore from "@/store/statusStore";
import { STATUS_MODULES } from "@/lib/statusManager";

/**
 * @param {{
 *   id?: string,
 *   value: string,
 *   onChange: (model: string) => void,
 *   className?: string,
 * }} props
 */
export default function OllamaModelPicker({ id, value, onChange, className }) {
  const ollamaModule = useStatusStore((s) => s.modules.ollama);
  const [refreshing, setRefreshing] = useState(false);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await STATUS_MODULES.ollama.check();
    } finally {
      setRefreshing(false);
    }
  };

  const handleOpenWizard = () => {
    // 로컬 AI 설정 위저드 — 설치/시작/모델 다운로드를 한 번에
    window.dispatchEvent(new CustomEvent("officeclaw:open-local-ai-setup"));
  };

  const models = Array.isArray(ollamaModule.models) ? ollamaModule.models : [];
  const isInstalled = !!ollamaModule.installed;
  const isRunning = !!ollamaModule.running;

  // ── 빈/오류 상태 ─────────────────────────────────────────────────────────
  if (!isInstalled) {
    return (
      <HintBox
        icon={AlertTriangle}
        tone="warning"
        title="로컬 AI 엔진이 설치되어 있지 않아요"
        description="모델을 선택하려면 먼저 로컬 AI 엔진을 설치해야 해요."
        actions={
          <>
            <Button size="sm" variant="outline" onClick={handleRefresh} disabled={refreshing}>
              {refreshing ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <RefreshCw className="mr-1 h-3 w-3" />}
              다시 확인
            </Button>
            <Button size="sm" onClick={handleOpenWizard}>
              자동 설치 마법사 열기
            </Button>
          </>
        }
        className={className}
      />
    );
  }

  if (!isRunning) {
    return (
      <HintBox
        icon={AlertTriangle}
        tone="warning"
        title="로컬 AI 엔진이 실행되고 있지 않아요"
        description="아래 [자동 시작] 버튼을 눌러주세요."
        actions={
          <>
            <Button size="sm" variant="outline" onClick={handleRefresh} disabled={refreshing}>
              {refreshing ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <RefreshCw className="mr-1 h-3 w-3" />}
              다시 확인
            </Button>
            <Button size="sm" onClick={handleOpenWizard}>
              자동 시작
            </Button>
          </>
        }
        className={className}
      />
    );
  }

  if (models.length === 0) {
    return (
      <HintBox
        icon={Download}
        tone="warning"
        title="설치된 AI 모델이 없어요"
        description="자동 설치 마법사에서 추천 모델을 받거나, 직접 받을 수도 있어요."
        actions={
          <>
            <Button size="sm" variant="outline" onClick={handleRefresh} disabled={refreshing}>
              {refreshing ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <RefreshCw className="mr-1 h-3 w-3" />}
              다시 확인
            </Button>
            <Button size="sm" onClick={handleOpenWizard}>
              자동 다운로드
            </Button>
          </>
        }
        className={className}
      />
    );
  }

  // ── 정상: 모델 선택 + 새로고침 ────────────────────────────────────────────
  // 현재 value가 설치 목록에 없으면 별도 항목으로 노출 (선택은 유지하되 경고)
  const valueInList = models.some((m) => m.name === value);

  return (
    <div className={className}>
      <div className="flex items-center gap-2">
        <Select value={value} onValueChange={onChange}>
          <SelectTrigger id={id} className="w-64">
            <SelectValue placeholder="모델 선택" />
          </SelectTrigger>
          <SelectContent>
            {models.map((m) => (
              <SelectItem key={m.name} value={m.name}>
                {m.name}
              </SelectItem>
            ))}
            {!valueInList && value && (
              <SelectItem value={value} className="text-muted-foreground italic">
                {value} (현재 설치 안 됨)
              </SelectItem>
            )}
          </SelectContent>
        </Select>
        <Button
          size="sm"
          variant="outline"
          onClick={handleRefresh}
          disabled={refreshing}
          title="모델 목록 새로고침"
          className="shrink-0"
        >
          {refreshing ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
        </Button>
      </div>
      {!valueInList && value && (
        <p className="mt-1 flex items-start gap-1 text-xs text-amber-600 dark:text-amber-400">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          저장된 모델 <strong className="mx-0.5">{value}</strong>이(가) 지금은 설치되어 있지 않아요.
          위에서 다른 모델을 선택하거나, 자동 설치 마법사에서 다시 받을 수 있어요.
        </p>
      )}
      <p className="mt-1 text-xs text-muted-foreground">
        설치된 모델 {models.length}개
      </p>
    </div>
  );
}

/**
 * 안내 박스 — 미설치/미실행/모델 없음 상태에서 일관된 모양으로 표시.
 *
 * 이 컴포넌트는 OllamaModelPicker 내부 헬퍼지만 같은 패턴으로 자주 쓰일 수 있어
 * 별도 함수로 분리. 추후 공용 UI primitive로 승격해도 됨.
 */
function HintBox({ icon: Icon, title, description, actions, className }) {
  return (
    <div
      className={`rounded-md border border-amber-200 bg-amber-50/60 p-3 text-sm dark:border-amber-900/40 dark:bg-amber-950/30 ${
        className ?? ""
      }`}
    >
      <div className="flex items-start gap-2">
        <Icon className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
        <div className="flex-1 space-y-1">
          <p className="font-medium text-amber-900 dark:text-amber-100">{title}</p>
          <p className="text-xs text-amber-800 dark:text-amber-200">{description}</p>
          {actions && <div className="mt-2 flex flex-wrap gap-2">{actions}</div>}
        </div>
      </div>
    </div>
  );
}
