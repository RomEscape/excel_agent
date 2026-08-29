"""민감 데이터 마스킹 관련 Pydantic 모델."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Detection(BaseModel):
    """단일 민감 데이터 감지 결과."""

    type: str                  # 예: "주민등록번호", "카드번호"
    placeholder: str           # 치환된 플레이스홀더 텍스트
    start: int                 # 원본 텍스트에서의 시작 위치
    end: int                   # 원본 텍스트에서의 끝 위치


class MaskResult(BaseModel):
    """masking_service.mask() 반환값."""

    masked_text: str
    detections: list[Detection] = Field(default_factory=list)
    was_modified: bool = False

    @property
    def count(self) -> int:
        return len(self.detections)

    @property
    def types(self) -> list[str]:
        """감지된 유형 목록 (중복 제거)."""
        seen: set[str] = set()
        result: list[str] = []
        for d in self.detections:
            if d.type not in seen:
                seen.add(d.type)
                result.append(d.type)
        return result
