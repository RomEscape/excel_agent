/**
 * DocumentModule — Full Document AI implementation.
 *
 * Layout:
 *  ┌─────────────────────────────────────────────┐
 *  │ 문서 유형 선택 (카드 그리드)                   │
 *  │ [보고서] [기획안] [회의록] [계약서초안]          │
 *  │ [이메일] [제안서]                             │
 *  ├─────────────────────────────────────────────┤
 *  │ 내용 입력                                    │
 *  │  핵심 내용 (textarea)                        │
 *  │  톤 선택: [공식적 / 친근한 / 전문적]            │
 *  │  길이 선택: [짧게 / 보통 / 길게]               │
 *  │  [초안 생성 버튼]                             │
 *  ├─────────────────────────────────────────────┤
 *  │ 생성된 초안 — 미리보기/편집 토글               │
 *  │  [Word로 저장] [PDF로 저장] [복사] 버튼        │
 *  └─────────────────────────────────────────────┘
 */

import React, { useState, useCallback } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Spinner } from "@/components/ui/spinner";
import { ErrorCard } from "@/components/ui/error-card";
import MarkdownViewer from "@/components/ui/markdown-viewer";

import { toUserMessage } from "@/lib/errorMessages";
import {
  documentGenerate,
  documentExportDocx,
  documentExportPdf,
} from "@/lib/api";

// ── Document type definitions ──────────────────────────────────────────────

const DOC_TYPES = [
  {
    id: "보고서",
    label: "보고서",
    description: "업무 현황 및 결과 보고",
    icon: "📄",
  },
  {
    id: "기획안",
    label: "기획안",
    description: "신규 사업 또는 프로젝트 기획",
    icon: "💡",
  },
  {
    id: "회의록",
    label: "회의록",
    description: "회의 내용 및 결정 사항 정리",
    icon: "📝",
  },
  {
    id: "계약서초안",
    label: "계약서 초안",
    description: "계약 조건 초안 (법률 검토 필요)",
    icon: "📋",
  },
  {
    id: "이메일",
    label: "이메일",
    description: "공식 비즈니스 이메일 작성",
    icon: "✉️",
  },
  {
    id: "제안서",
    label: "제안서",
    description: "고객 또는 파트너 제안 문서",
    icon: "🤝",
  },
];

const TONE_OPTIONS = [
  { value: "공식적", label: "공식적" },
  { value: "친근한", label: "친근한" },
  { value: "전문적", label: "전문적" },
];

const LENGTH_OPTIONS = [
  { value: "짧게", label: "짧게" },
  { value: "보통", label: "보통" },
  { value: "길게", label: "길게" },
];

// ── Sub-components ──────────────────────────────────────────────────────────

/** Toggle-style button group for tone/length selection */
function ToggleGroup({ options, value, onChange, ariaLabel }) {
  return (
    <div className="flex gap-2" role="group" aria-label={ariaLabel}>
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          aria-pressed={value === opt.value}
          className={[
            "rounded-md border px-3 py-1.5 text-sm font-medium transition-colors",
            value === opt.value
              ? "border-primary bg-primary text-primary-foreground"
              : "border-border bg-background hover:bg-muted",
          ].join(" ")}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

/** Markdown preview of the generated draft — delegates to shared MarkdownViewer */
function DraftPreview({ content }) {
  return <MarkdownViewer content={content} />;
}

// ── Main component ────────────────────────────────────────────────────────────

export default function DocumentModule() {
  const [selectedType, setSelectedType] = useState(null);
  const [keyContent, setKeyContent] = useState("");
  const [tone, setTone] = useState("공식적");
  const [length, setLength] = useState("보통");

  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState(null);
  const [draft, setDraft] = useState("");

  // Preview / edit mode toggle for the generated draft
  const [draftMode, setDraftMode] = useState("preview"); // "preview" | "edit"

  // Export state
  const [exportDocxStatus, setExportDocxStatus] = useState(null);
  const [exportPdfStatus, setExportPdfStatus] = useState(null);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(null);

  // ── Handlers ──────────────────────────────────────────────────────────────

  async function handleGenerate() {
    if (!selectedType || !keyContent.trim()) return;

    // Confirm before discarding an existing draft (m-5: prevent accidental loss)
    if (draft.length > 0) {
      const confirmed = window.confirm(
        "현재 초안을 지우고 새로 생성하시겠습니까?\n수정 중인 내용이 사라집니다."
      );
      if (!confirmed) return;
    }

    setGenerating(true);
    setGenerateError(null);
    setDraft("");
    setDraftMode("preview");
    setExportDocxStatus(null);
    setExportPdfStatus(null);
    try {
      const res = await documentGenerate(
        selectedType,
        keyContent.trim(),
        tone,
        length
      );
      setDraft(res.draft ?? res);
    } catch (err) {
      setGenerateError(toUserMessage(err));
    } finally {
      setGenerating(false);
    }
  }

  async function handleExportDocx() {
    if (!draft) return;
    setExportDocxStatus("loading");
    try {
      const savedPath = await documentExportDocx(selectedType, draft);
      setExportDocxStatus({ path: savedPath });
    } catch (err) {
      setExportDocxStatus({ error: toUserMessage(err) });
    }
  }

  async function handleExportPdf() {
    if (!draft) return;
    setExportPdfStatus("loading");
    try {
      const savedPath = await documentExportPdf(selectedType, draft);
      setExportPdfStatus({ path: savedPath });
    } catch (err) {
      setExportPdfStatus({ error: toUserMessage(err) });
    }
  }

  async function handleCopy() {
    if (!draft) return;
    setCopyError(null);
    try {
      await navigator.clipboard.writeText(draft);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API may not be available in all Tauri WebView contexts
      setCopyError("복사에 실패했습니다. 초안을 직접 선택하여 복사해 주세요.");
      setTimeout(() => setCopyError(null), 4000);
    }
  }

  const canGenerate = Boolean(selectedType && keyContent.trim() && !generating);
  const hasDraft = draft.length > 0;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">문서 AI</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          AI가 업무 문서 초안을 작성합니다. 유형을 선택하고 핵심 내용을 입력하세요.
        </p>
      </div>

      {/* Step 1 — Document type selection */}
      <div className="space-y-3">
        <Label className="text-sm font-semibold">1. 문서 유형 선택</Label>
        <div
          className="grid grid-cols-2 gap-3 sm:grid-cols-3"
          role="radiogroup"
          aria-label="문서 유형"
        >
          {DOC_TYPES.map((type) => {
            const isSelected = selectedType === type.id;
            return (
              <button
                key={type.id}
                type="button"
                role="radio"
                aria-checked={isSelected}
                onClick={() => setSelectedType(type.id)}
                className={[
                  "rounded-xl border p-4 text-left transition-all",
                  isSelected
                    ? "border-primary bg-primary/5 ring-1 ring-primary"
                    : "border-border hover:border-primary/40 hover:bg-muted/30",
                ].join(" ")}
              >
                <div className="mb-1 text-xl" aria-hidden="true">{type.icon}</div>
                <div className="text-sm font-medium">{type.label}</div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {type.description}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Step 2 — Content + options */}
      <div className="space-y-4">
        <Label className="text-sm font-semibold">2. 내용 입력</Label>

        <div className="space-y-1.5">
          <Label htmlFor="key-content" className="text-xs text-muted-foreground">
            핵심 내용
            {selectedType && (
              <span className="ml-1 text-primary">({selectedType})</span>
            )}
          </Label>
          <Textarea
            id="key-content"
            value={keyContent}
            onChange={(e) => setKeyContent(e.target.value)}
            placeholder={
              selectedType
                ? `${selectedType}에 들어갈 핵심 내용을 자유롭게 입력하세요. 주요 사실, 날짜, 관련자, 결론 등을 포함하면 더 좋은 초안이 생성됩니다.`
                : "먼저 문서 유형을 선택해 주세요."
            }
            className="min-h-[120px] resize-y"
            disabled={!selectedType}
          />
        </div>

        <div className="flex flex-wrap gap-6">
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">문체 (톤)</Label>
            <ToggleGroup
              options={TONE_OPTIONS}
              value={tone}
              onChange={setTone}
              ariaLabel="문체 선택"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">길이</Label>
            <ToggleGroup
              options={LENGTH_OPTIONS}
              value={length}
              onChange={setLength}
              ariaLabel="길이 선택"
            />
          </div>
        </div>

        <Button
          onClick={handleGenerate}
          disabled={!canGenerate}
          className="w-full sm:w-auto"
          size="lg"
        >
          {generating ? "초안 생성 중..." : "초안 생성"}
        </Button>
      </div>

      {generating && (
        <Spinner label="AI가 문서 초안을 작성 중입니다..." />
      )}
      {generateError && <ErrorCard message={generateError} />}

      {/* Step 3 — Generated draft */}
      {hasDraft && (
        <div className="space-y-4">
          <Separator />
          <div className="flex items-center justify-between">
            <Label className="text-sm font-semibold">3. 생성된 초안</Label>
            <div className="flex items-center gap-2">
              <Badge variant="secondary" className="text-xs">
                {selectedType}
              </Badge>
              {/* Preview / Edit toggle */}
              <div
                className="flex rounded-md border border-border overflow-hidden"
                role="group"
                aria-label="초안 보기 모드"
              >
                <button
                  type="button"
                  onClick={() => setDraftMode("preview")}
                  aria-pressed={draftMode === "preview"}
                  className={[
                    "px-3 py-1 text-xs font-medium transition-colors",
                    draftMode === "preview"
                      ? "bg-primary text-primary-foreground"
                      : "bg-background hover:bg-muted",
                  ].join(" ")}
                >
                  미리보기
                </button>
                <button
                  type="button"
                  onClick={() => setDraftMode("edit")}
                  aria-pressed={draftMode === "edit"}
                  className={[
                    "px-3 py-1 text-xs font-medium transition-colors",
                    draftMode === "edit"
                      ? "bg-primary text-primary-foreground"
                      : "bg-background hover:bg-muted",
                  ].join(" ")}
                >
                  편집
                </button>
              </div>
            </div>
          </div>

          {draftMode === "preview" ? (
            <DraftPreview content={draft} />
          ) : (
            <>
              <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                className="min-h-[320px] resize-y font-mono text-sm"
                placeholder="생성된 초안이 여기에 표시됩니다."
                aria-label="초안 편집"
              />
              <p className="text-xs text-muted-foreground">
                마크다운 형식으로 편집할 수 있습니다 (## 제목, **굵게**, - 목록).
                미리보기 탭에서 렌더링된 결과를 확인하세요.
              </p>
            </>
          )}

          {/* Export actions */}
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="default"
              onClick={handleExportDocx}
              disabled={exportDocxStatus === "loading"}
            >
              {exportDocxStatus === "loading" ? "저장 중..." : "Word로 저장"}
            </Button>
            <Button
              variant="outline"
              onClick={handleExportPdf}
              disabled={exportPdfStatus === "loading"}
            >
              {exportPdfStatus === "loading" ? "저장 중..." : "PDF로 저장"}
            </Button>
            <Button type="button" variant="ghost" onClick={handleCopy}>
              {copied ? "복사됨!" : "복사"}
            </Button>
          </div>

          {/* Clipboard error */}
          {copyError && (
            <p className="text-xs text-destructive">{copyError}</p>
          )}

          {/* Export status messages */}
          <div className="space-y-1">
            {exportDocxStatus?.path && (
              <p className="text-xs text-green-600">
                Word 저장 완료: {exportDocxStatus.path}
              </p>
            )}
            {exportDocxStatus?.error && (
              <p className="text-xs text-destructive">{exportDocxStatus.error}</p>
            )}
            {exportPdfStatus?.path && (
              <p className="text-xs text-green-600">
                PDF 저장 완료: {exportPdfStatus.path}
              </p>
            )}
            {exportPdfStatus?.error && (
              <p className="text-xs text-destructive">{exportPdfStatus.error}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
