"""LLM 응답 텍스트에서 JSON 오브젝트를 꺼낸다.

`re.search(r"\\{.*\\}", raw, re.DOTALL)` 하나로 버티고 있었다. 이 정규식은 첫 `{`부터
**마지막** `}`까지를 통째로 집는다. 응답에 JSON이 하나뿐이고 그 뒤에 아무것도 없을
때만 맞는다. 다음은 전부 깨진다.

- 오브젝트가 둘 이상 — `{"초안":1}` 뒤에 진짜 답이 오면 둘을 하나로 이어 붙인다
- JSON 뒤에 설명 문장 — 문장에 `}`가 있으면 거기까지 삼킨다
- 사고형 모델의 `<think>` 블록 — 그 안의 중괄호가 그대로 섞인다

지금 기본 플래너(`ax7bplanner-*`)는 맨 JSON만 뱉으므로 실제 로그 42건에서 실패가
없었다. 하지만 사용자는 설정에서 아무 Ollama 모델이나 고를 수 있고, 프로바이더를
Claude로 바꾸면 앞뒤로 설명이 붙는 응답이 흔하다. 모델을 바꿨다고 플래너가 통째로
멎으면 안 된다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

# 사고 과정을 닫는 태그. 여는 태그 없이 닫는 태그만 오는 응답도 있어서 이쪽을 기준으로 자른다.
_THINK_CLOSE = re.compile(r"</think\s*>", re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think\s*>", re.IGNORECASE)
# ```json / ``` — 울타리 자체만 걷어내고 안쪽은 남긴다.
_CODE_FENCE = re.compile(r"```[A-Za-z0-9_+-]*")


def strip_reasoning(raw: str) -> str:
    """사고 블록과 코드 울타리를 걷어낸 본문을 돌려준다.

    사고 블록이 닫혀 있으면 **마지막** `</think>` 뒤만 남긴다. 그 앞은 모델이
    혼잣말한 것이라 중간 초안 JSON이 섞여 있을 수 있다.
    """
    text = str(raw or "")
    closings = list(_THINK_CLOSE.finditer(text))
    if closings:
        text = text[closings[-1].end() :]
    elif _THINK_OPEN.search(text):
        # 열기만 하고 닫지 않았다면 어디까지가 혼잣말인지 알 수 없다. 통째로 훑되
        # 균형 스캐너가 완결된 오브젝트만 집어내도록 그대로 둔다.
        pass
    return _CODE_FENCE.sub("", text)


def iter_json_objects(text: str) -> Iterator[str]:
    """최상위에서 균형이 맞는 `{...}` 구간을 앞에서부터 하나씩 내놓는다.

    문자열 리터럴 안의 중괄호는 세지 않는다. `{"reason": "A1 } 참고"}` 같은 값이
    실제로 들어온다.
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth == 0:
                # 짝 없는 닫는 괄호. 앞이 잘려 들어온 응답에서 나온다.
                continue
            depth -= 1
            if depth == 0 and start >= 0:
                yield text[start : index + 1]
                start = -1


def extract_json_object(
    raw: str,
    *,
    require_keys: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """응답에서 JSON 오브젝트 하나를 꺼낸다. 못 찾으면 None.

    `require_keys`를 주면 그 키를 가진 오브젝트를 우선한다. 모델이 프롬프트의 출력
    예시를 먼저 따라 쓰고 진짜 답을 뒤에 붙이는 경우가 있어서, 앞에서부터 무조건
    첫 번째를 집으면 예시를 실행하게 된다. 조건에 맞는 것이 없으면 파싱에 성공한
    첫 오브젝트를 돌려준다 — 아무것도 안 주는 것보다는 낫다.
    """
    fallback: dict[str, Any] | None = None
    for chunk in iter_json_objects(strip_reasoning(raw)):
        try:
            parsed = json.loads(chunk)
        except ValueError:
            continue
        if not isinstance(parsed, dict):
            continue
        if require_keys and not any(key in parsed for key in require_keys):
            if fallback is None:
                fallback = parsed
            continue
        return parsed
    return fallback
