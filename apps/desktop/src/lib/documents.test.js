import test from "node:test";
import assert from "node:assert/strict";

import {
  DOCUMENT_GRID_LIMIT,
  buildDocumentGrid,
  documentKind,
  extensionOf,
  relativeDay,
} from "./documents.js";

/** unix seconds 헬퍼 — 테스트가 시각을 고정할 수 있게 한다. */
const at = (y, m, d, h = 12) => new Date(y, m - 1, d, h).getTime() / 1000;

test("extensionOf: 점 위치를 정확히 다룬다", () => {
  assert.equal(extensionOf("보고서.xlsx"), "xlsx");
  assert.equal(extensionOf("a.b.c.PDF"), "pdf");
  assert.equal(extensionOf("확장자없음"), "");
  // 숨김 파일은 확장자가 아니라 이름이다.
  assert.equal(extensionOf(".gitignore"), "");
  // 점으로 끝나면 확장자가 없는 것과 같다.
  assert.equal(extensionOf("끝점."), "");
  assert.equal(extensionOf(undefined), "");
});

test("documentKind: 업무 문서만 통과시킨다", () => {
  assert.equal(documentKind("매출.xlsx"), "excel");
  assert.equal(documentKind("데이터.csv"), "excel");
  assert.equal(documentKind("기획.docx"), "word");
  assert.equal(documentKind("발표.pptx"), "powerpoint");
  assert.equal(documentKind("계약.pdf"), "pdf");
  // 스크립트·로그가 홈 그리드에 깔리면 홈이 파일 탐색기가 된다.
  assert.equal(documentKind("build.log"), null);
  assert.equal(documentKind("main.py"), null);
});

test("relativeDay: 경과 시간이 아니라 날짜 경계로 센다", () => {
  const now = new Date(2026, 7, 12, 1, 0); // 8/12 새벽 1시
  // 어제 23시 파일 — 경과는 2시간이지만 날짜로는 어제다.
  assert.equal(relativeDay(at(2026, 8, 11, 23), now), "어제");
  assert.equal(relativeDay(at(2026, 8, 12, 0, 30), now), "오늘");
  assert.equal(relativeDay(at(2026, 8, 9, 12), now), "3일 전");
});

test("relativeDay: 값이 없거나 깨지면 빈 문자열", () => {
  const now = new Date(2026, 7, 12);
  assert.equal(relativeDay(0, now), "");
  assert.equal(relativeDay(undefined, now), "");
  assert.equal(relativeDay(Number.NaN, now), "");
});

test("buildDocumentGrid: 최근 수정 순으로 자르고 나머지를 센다", () => {
  const now = new Date(2026, 7, 12);
  const files = [
    { name: "오래된.xlsx", path: "a.xlsx", modified: at(2026, 8, 5) },
    { name: "최신.xlsx", path: "b.xlsx", modified: at(2026, 8, 11) },
    { name: "중간.docx", path: "c.docx", modified: at(2026, 8, 9) },
  ];

  const grid = buildDocumentGrid(files, now, 2);
  assert.deepEqual(
    grid.cards.map((c) => c.name),
    ["최신.xlsx", "중간.docx"]
  );
  assert.equal(grid.remaining, 1);
  assert.equal(grid.total, 3);
  assert.equal(grid.cards[0].age, "어제");
});

test("buildDocumentGrid: 폴더와 비문서를 빼고 센다", () => {
  const now = new Date(2026, 7, 12);
  const files = [
    { name: "폴더", path: "d", is_dir: true, modified: at(2026, 8, 11) },
    { name: "노트.txt", path: "n.txt", modified: at(2026, 8, 11) },
    { name: "매출.xlsx", path: "s.xlsx", modified: at(2026, 8, 11) },
  ];

  const grid = buildDocumentGrid(files, now);
  // 폴더/txt가 total에 섞이면 "더보기" 숫자가 실제 문서 수와 어긋난다.
  assert.equal(grid.total, 1);
  assert.equal(grid.remaining, 0);
  assert.equal(grid.cards[0].name, "매출.xlsx");
});

test("buildDocumentGrid: 입력이 없거나 깨져도 빈 그리드를 준다", () => {
  for (const input of [null, undefined, "nope", []]) {
    const grid = buildDocumentGrid(input, new Date());
    assert.deepEqual(grid.cards, []);
    assert.equal(grid.remaining, 0);
    assert.equal(grid.total, 0);
  }
});

test("buildDocumentGrid: 잘못된 limit은 기본값으로 접힌다", () => {
  const now = new Date(2026, 7, 12);
  const files = Array.from({ length: 10 }, (_, i) => ({
    name: `f${i}.xlsx`,
    path: `f${i}.xlsx`,
    modified: at(2026, 8, 10),
  }));

  for (const bad of [0, -3, 1.5, "여섯", undefined]) {
    const grid = buildDocumentGrid(files, now, bad);
    assert.equal(grid.cards.length, DOCUMENT_GRID_LIMIT);
    assert.equal(grid.remaining, 10 - DOCUMENT_GRID_LIMIT);
  }
});
