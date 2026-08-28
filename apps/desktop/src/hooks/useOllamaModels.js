/**
 * useOllamaModels — 설치된 로컬 AI 모델 목록을 셀렉트 옵션으로 구독한다.
 *
 * 데이터 소스는 **중앙 `statusStore.modules.ollama` 하나뿐**이다.
 * `STATUS_MODULES.ollama.check()`가 Rust `ollama_status`(= `/api/tags`,
 * `ollama list`가 보는 것과 같은 목록)를 읽어 store에 넣고, App 루트의
 * `useStatusPoller`가 30초마다 갱신한다.
 *
 * 화면이 각자 `healthCheck()`를 부르지 않는 이유가 이것이다 — 같은 목록을
 * 두 경로로 받으면 한쪽만 갱신돼서 마법사와 설정이 서로 다른 모델을 보여준다.
 *
 * @param {{ extraIds?: string[] }} [opts]
 *   extraIds — 설치돼 있지 않아도 항목으로 보여줄 모델 ID.
 *   설치 마법사의 "받을 수 있는 모델", 설정의 "저장돼 있는데 지금은 없는 모델"이
 *   여기로 들어온다. `installed: false`로 표시된다.
 */
import { useCallback, useMemo, useState } from "react";

import useStatusStore from "@/store/statusStore";
import { STATUS_MODULES } from "@/lib/statusManager";
import { buildModelChoices, pickDefaultModel } from "@/lib/modelCatalog";

export function useOllamaModels(opts = {}) {
  const extraIds = opts.extraIds;
  const ollamaModule = useStatusStore((s) => s.modules.ollama);
  const [refreshing, setRefreshing] = useState(false);

  // 배열 리터럴을 그대로 의존성에 넣으면 매 렌더 새 참조가 되어 useMemo가 무력해진다.
  const extraKey = Array.isArray(extraIds) ? extraIds.filter(Boolean).join(" ") : "";
  const models = ollamaModule.models;

  const options = useMemo(
    () => buildModelChoices(models, extraKey ? extraKey.split(" ") : []),
    [models, extraKey]
  );

  /** 목록 수동 새로고침 — 마법사에서 pull 직후, 설정에서 새로고침 버튼. */
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await STATUS_MODULES.ollama.check();
    } finally {
      setRefreshing(false);
    }
  }, []);

  const pickDefault = useCallback(
    (preferred) => pickDefaultModel(options, preferred),
    [options]
  );

  return {
    /** 셀렉트에 그대로 넘기는 옵션 배열 (추천 → 설치됨 → 이름순) */
    options,
    /** 실제로 받아져 있는 모델 수 — 안내 문구 분기용 */
    installedCount: options.filter((o) => o.installed).length,
    installed: !!ollamaModule.installed,
    running: !!ollamaModule.running,
    /** 아직 한 번도 check가 끝나지 않은 상태 — 로딩 표시용 */
    loading: ollamaModule.state === "unknown",
    refreshing,
    refresh,
    /** 저장된 선택 > 추천 > 첫 항목 */
    pickDefault,
  };
}

export default useOllamaModels;
