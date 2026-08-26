/**
 * ShortcutHelp — 단축키 도움말 모달.
 *
 * 트리거: `?` 또는 `Cmd/Ctrl+/` (Layout이 글로벌 키 처리)
 * 1-screen 그리드로 카테고리별 모든 단축키 노출.
 *
 * 껍데기(오버레이·Esc·바깥 클릭·닫기 버튼·포커스)는 `ui/modal.jsx`가 소유한다 —
 * 이 파일은 단축키 목록이라는 내용만 갖는다. 예전에는 같은 규칙을 여기에 한 벌
 * 더 갖고 있어서, 모달 동작을 고칠 때 한쪽만 고쳐지는 자리였다.
 *
 * `layer="top"`인 이유: 명령 팔레트(z-[1000])를 열어둔 채로도 `Cmd/Ctrl+/`가
 * 먹기 때문에, 기본 층(z-50)에 두면 도움말이 팔레트 뒤에 깔린다.
 */
import React from "react";
import { Keyboard } from "lucide-react";

import Modal from "@/components/ui/modal";

// 카테고리별 단축키 정의 (라벨/키)
const SHORTCUTS = [
  {
    group: "탐색",
    items: [
      { keys: ["⌘/Ctrl", "K"], label: "명령 팔레트 열기/닫기" },
      { keys: ["⌘/Ctrl", "B"], label: "사이드바 접기/펼치기" },
      { keys: ["⌘/Ctrl", "J"], label: "채팅 패널 열기/닫기" },
      { keys: ["⌘/Ctrl", "⇧", "L"], label: "화면 테마 전환 (라이트 ↔ 다크)" },
      { keys: ["?"], label: "이 도움말 열기" },
      { keys: ["⌘/Ctrl", "/"], label: "이 도움말 열기" },
      { keys: ["Esc"], label: "모달/팝오버 닫기" },
    ],
  },
  {
    group: "승인",
    items: [
      { keys: ["Y"], label: "승인 (보안 확인 · 엑셀 인라인 승인)" },
      { keys: ["Enter"], label: "승인" },
      { keys: ["N"], label: "거부" },
      { keys: ["Esc"], label: "거부 / 닫기" },
    ],
  },
  {
    group: "팔레트 내부",
    items: [
      { keys: ["↑", "↓"], label: "항목 이동" },
      { keys: ["Tab"], label: "그룹 점프" },
      { keys: ["Enter"], label: "선택 실행" },
    ],
  },
];

function KeyChip({ k }) {
  return (
    <kbd className="inline-flex h-5 min-w-[20px] items-center justify-center rounded border border-border bg-muted px-1.5 text-[10px] font-mono font-semibold text-foreground/80 shadow-[0_1px_0_0_rgba(0,0,0,0.04)]">
      {k}
    </kbd>
  );
}

function ShortcutRow({ keys, label }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <span className="truncate text-sm text-foreground">{label}</span>
      <span className="flex shrink-0 items-center gap-1">
        {keys.map((k, i) => (
          <React.Fragment key={i}>
            {i > 0 && <span className="text-[10px] text-muted-foreground">+</span>}
            <KeyChip k={k} />
          </React.Fragment>
        ))}
      </span>
    </div>
  );
}

export default function ShortcutHelp({ open, onClose }) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="단축키"
      size="lg"
      layer="top"
      icon={Keyboard}
      footer={
        <>
          <span className="text-[11px] text-muted-foreground">
            입력 필드 포커스 시 단일키 단축키는 비활성화됩니다 · 한글/일본어 입력 중에도 안전.
          </span>
          <span className="shrink-0 text-[11px] text-muted-foreground">Esc 닫기</span>
        </>
      }
    >
      <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
        {SHORTCUTS.map((section) => (
          <div key={section.group}>
            <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              {section.group}
            </h3>
            <div className="divide-y divide-border/60">
              {section.items.map((it, idx) => (
                <ShortcutRow key={idx} keys={it.keys} label={it.label} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}
