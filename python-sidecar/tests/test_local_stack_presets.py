"""로컬 스택 프리셋 기본값 검증."""

from office_claw_sidecar.local_stack import (
    DEFAULT_PRESET_ID,
    QWEN3_LOCAL,
    get_default_llm_config,
    get_preset,
)


def test_default_preset_is_the_openclaw_stack():
    # QWEN3_LOCAL은 예전 이름의 별칭이다. 둘이 같은 프리셋을 가리켜야 기존 설정이 깨지지 않는다.
    assert DEFAULT_PRESET_ID == "qwen3-openclaw"
    assert get_preset()["id"] == QWEN3_LOCAL["id"] == DEFAULT_PRESET_ID


def test_default_llm_config_matches_ollama_adot():
    cfg = get_default_llm_config()
    # 범용 대화는 베이스 모델, Excel Live 계획 수립은 파인튜닝본으로 나뉜다.
    # 파인튜닝본은 계획 JSON만 뱉도록 학습돼 일반 대화에 쓰면 안 된다.
    assert cfg == {
        "provider": "ollama",
        "model": "skt/A.X-4.0-Light:latest",
        "planner_model": "ax7bplanner-v3:latest",
    }
