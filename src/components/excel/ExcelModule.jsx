/**
 * ExcelModule — Full Excel AI implementation.
 *
 * Layout:
 *  ┌─────────────────────────────────────────────┐
 *  │ 파일 업로드 영역 (drag & drop or click)        │
 *  ├─────────────────────────────────────────────┤
 *  │ 업로드된 파일 정보 (시트 선택, 행수, 열수)       │
 *  ├──────────────────┬──────────────────────────┤
 *  │ 탭: 데이터 분석   │ 리포트 생성 │ 수식 제안     │
 *  ├──────────────────┴──────────────────────────┤
 *  │ 탭별 AI 응답 영역 + 차트 미리보기              │
 *  │ Excel로 내보내기 버튼                         │
 *  └─────────────────────────────────────────────┘
 */

import React, { useState, useCallback } from "react";
import { useDropzone } from "react-dropzone";
import { UploadCloud } from "lucide-react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  ArcElement,
  PointElement,
  Title,
  Tooltip,
  Legend,
} from "chart.js";
import { Bar, Line, Pie } from "react-chartjs-2";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Spinner } from "@/components/ui/spinner";
import { ErrorCard } from "@/components/ui/error-card";
import MarkdownViewer from "@/components/ui/markdown-viewer";
import EmptyState from "@/components/ui/empty-state";

import { toUserMessage } from "@/lib/errorMessages";
import {
  excelUpload,
  excelAnalyze,
  excelReport,
  excelFormulas,
  excelChartData,
  excelExport,
} from "@/lib/api";

// Register required Chart.js components once at module level
ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  ArcElement,
  PointElement,
  Title,
  Tooltip,
  Legend
);

// ── Constants ────────────────────────────────────────────────────────────────

const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50 MB — must match Python + Rust limits

const TABS = [
  { id: "analyze", label: "데이터 분석" },
  { id: "report", label: "리포트 생성" },
  { id: "formulas", label: "수식 제안" },
];

const CHART_TYPES = [
  { id: "bar", label: "막대" },
  { id: "line", label: "꺾은선" },
  { id: "pie", label: "원형" },
];

const PALETTE = [
  "rgba(59,130,246,0.7)",
  "rgba(16,185,129,0.7)",
  "rgba(245,158,11,0.7)",
  "rgba(239,68,68,0.7)",
  "rgba(139,92,246,0.7)",
  "rgba(236,72,153,0.7)",
  "rgba(20,184,166,0.7)",
  "rgba(249,115,22,0.7)",
];

// ── Sub-components ───────────────────────────────────────────────────────────

/** Status badge shown next to tab labels when content is available. */
function DotBadge() {
  return (
    <span className="ml-1.5 inline-block h-2 w-2 rounded-full bg-green-500" />
  );
}

/** Markdown renderer — delegates to shared MarkdownViewer (no border/bg here, just prose) */
function MarkdownView({ content }) {
  return (
    <MarkdownViewer content={content} className="border-0 bg-transparent p-0" />
  );
}

/**
 * File metadata info card shown after successful upload.
 * Includes a sheet selector dropdown when the file has multiple sheets.
 */
function FileMetaCard({ meta, activeSheet, onSheetChange }) {
  if (!meta) return null;
  return (
    <Card className="bg-muted/30">
      <CardContent className="flex flex-wrap gap-4 py-3">
        <div className="text-sm">
          <span className="font-medium text-muted-foreground">파일명: </span>
          <span>{meta.filename}</span>
        </div>
        <Separator orientation="vertical" className="h-5" />
        <div className="flex items-center gap-1.5 text-sm">
          <span className="font-medium text-muted-foreground">시트: </span>
          {meta.sheet_names?.length > 1 ? (
            <select
              value={activeSheet}
              onChange={(e) => onSheetChange(e.target.value)}
              className="rounded border border-border bg-background px-1.5 py-0.5 text-sm"
              aria-label="분석할 시트 선택"
            >
              {meta.sheet_names.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          ) : (
            <span>{meta.active_sheet}</span>
          )}
        </div>
        <Separator orientation="vertical" className="h-5" />
        <div className="text-sm">
          <span className="font-medium text-muted-foreground">행: </span>
          <span>{meta.row_count?.toLocaleString()}개</span>
        </div>
        <Separator orientation="vertical" className="h-5" />
        <div className="text-sm">
          <span className="font-medium text-muted-foreground">열: </span>
          <span>{meta.col_count}개</span>
        </div>
        {meta.columns?.length > 0 && (
          <>
            <Separator orientation="vertical" className="h-5" />
            <div className="flex flex-wrap gap-1 text-sm">
              <span className="font-medium text-muted-foreground">열 목록: </span>
              {meta.columns.slice(0, 6).map((col) => (
                <Badge key={col} variant="secondary" className="text-xs">
                  {col}
                </Badge>
              ))}
              {meta.columns.length > 6 && (
                <Badge variant="outline" className="text-xs">
                  +{meta.columns.length - 6}
                </Badge>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** Chart preview with Bar / Line / Pie toggle */
function ChartPreview({ chartData, chartType, onChartTypeChange }) {
  if (!chartData || !chartData.datasets?.length) return null;

  const datasets = chartData.datasets.map((ds, i) => ({
    label: ds.label,
    data: ds.data,
    backgroundColor: PALETTE[i % PALETTE.length],
    borderColor: PALETTE[i % PALETTE.length].replace("0.7", "1"),
    borderRadius: chartType === "bar" ? 4 : undefined,
    fill: false,
    tension: 0.3,
  }));

  const data = { labels: chartData.labels, datasets };

  const options = {
    responsive: true,
    maintainAspectRatio: true,
    plugins: {
      legend: { position: "top" },
      title: { display: false },
    },
  };

  const ChartComponent = chartType === "line" ? Line : chartType === "pie" ? Pie : Bar;

  return (
    <div className="mt-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-medium text-muted-foreground">차트 미리보기</h3>
        <div className="flex gap-1" role="group" aria-label="차트 유형 선택">
          {CHART_TYPES.map((ct) => (
            <button
              key={ct.id}
              onClick={() => onChartTypeChange(ct.id)}
              aria-pressed={chartType === ct.id}
              className={[
                "rounded px-2 py-1 text-xs font-medium transition-colors",
                chartType === ct.id
                  ? "bg-primary text-primary-foreground"
                  : "border border-border bg-background hover:bg-muted",
              ].join(" ")}
            >
              {ct.label}
            </button>
          ))}
        </div>
      </div>
      <div className="rounded-lg border bg-background p-4">
        <ChartComponent data={data} options={options} />
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function ExcelModule() {
  const [activeTab, setActiveTab] = useState("analyze");
  const [question, setQuestion] = useState("");

  // Upload state
  const [fileMeta, setFileMeta] = useState(null);
  const [activeSheet, setActiveSheet] = useState("");
  const [uploadError, setUploadError] = useState(null);
  const [uploading, setUploading] = useState(false);

  // Per-tab result state
  const [analyzeResult, setAnalyzeResult] = useState(null);
  const [analyzeError, setAnalyzeError] = useState(null);
  const [analyzingData, setAnalyzingData] = useState(false);

  const [reportResult, setReportResult] = useState(null);
  const [reportError, setReportError] = useState(null);
  const [generatingReport, setGeneratingReport] = useState(false);

  const [formulasResult, setFormulasResult] = useState(null);
  const [formulasError, setFormulasError] = useState(null);
  const [loadingFormulas, setLoadingFormulas] = useState(false);

  const [chartData, setChartData] = useState(null);
  const [chartType, setChartType] = useState("bar");

  const [exportStatus, setExportStatus] = useState(null);

  // ── File upload via react-dropzone ────────────────────────────────────────

  const onDrop = useCallback(async (acceptedFiles, rejectedFiles) => {
    // Handle rejections from the dropzone (e.g., size limit)
    if (rejectedFiles?.length) {
      const firstError = rejectedFiles[0]?.errors?.[0];
      if (firstError?.code === "file-too-large") {
        setUploadError("파일 크기가 50MB를 초과합니다. 더 작은 파일을 사용해 주세요.");
      } else {
        setUploadError("파일을 업로드할 수 없습니다. 파일 형식과 크기를 확인해 주세요.");
      }
      return;
    }

    if (!acceptedFiles.length) return;
    const file = acceptedFiles[0];

    setUploadError(null);
    setFileMeta(null);
    setActiveSheet("");
    setAnalyzeResult(null);
    setReportResult(null);
    setFormulasResult(null);
    setChartData(null);
    setExportStatus(null);
    setUploading(true);

    try {
      const filePath = file.path;
      if (!filePath) {
        throw new Error("파일 경로를 읽을 수 없습니다. 앱을 통해 파일을 선택해 주세요.");
      }

      const result = await excelUpload(filePath);
      const sheet = result.active_sheet || "";
      setFileMeta({ ...result, filename: file.name });
      setActiveSheet(sheet);

      // Auto-select chart type based on data shape (date columns → line chart)
      const hasDateColumn = Object.values(result.dtypes ?? {}).some(
        (t) => t === "날짜"
      );
      setChartType(hasDateColumn ? "line" : "bar");

      // Eagerly load chart data in the background
      if (result.file_id) {
        excelChartData(result.file_id, sheet)
          .then((cd) => setChartData(cd))
          .catch(() => {
            // Chart data is non-critical — silently skip if unavailable
          });
      }
    } catch (err) {
      setUploadError(toUserMessage(err));
    } finally {
      setUploading(false);
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
      "application/vnd.ms-excel": [".xls"],
      "text/csv": [".csv"],
    },
    multiple: false,
    disabled: uploading,
    maxSize: MAX_FILE_SIZE,
  });

  // ── Sheet change handler ───────────────────────────────────────────────────

  const handleSheetChange = useCallback(
    async (newSheet) => {
      if (!fileMeta?.file_id || newSheet === activeSheet) return;
      setActiveSheet(newSheet);
      // Reload chart data for the newly selected sheet
      try {
        const cd = await excelChartData(fileMeta.file_id, newSheet);
        setChartData(cd);
      } catch {
        setChartData(null);
      }
    },
    [fileMeta, activeSheet]
  );

  // ── AI actions ────────────────────────────────────────────────────────────

  async function handleAnalyze() {
    if (!fileMeta?.file_id || !question.trim()) return;
    setAnalyzingData(true);
    setAnalyzeError(null);
    setAnalyzeResult(null);
    try {
      const res = await excelAnalyze(fileMeta.file_id, question.trim());
      setAnalyzeResult(res.answer ?? res);
    } catch (err) {
      setAnalyzeError(toUserMessage(err));
    } finally {
      setAnalyzingData(false);
    }
  }

  async function handleGenerateReport() {
    if (!fileMeta?.file_id) return;
    setGeneratingReport(true);
    setReportError(null);
    setReportResult(null);
    try {
      const res = await excelReport(fileMeta.file_id);
      setReportResult(res.report ?? res);
    } catch (err) {
      setReportError(toUserMessage(err));
    } finally {
      setGeneratingReport(false);
    }
  }

  async function handleSuggestFormulas() {
    if (!fileMeta?.file_id) return;
    setLoadingFormulas(true);
    setFormulasError(null);
    setFormulasResult(null);
    try {
      const res = await excelFormulas(fileMeta.file_id);
      setFormulasResult(res.suggestions ?? res);
    } catch (err) {
      setFormulasError(toUserMessage(err));
    } finally {
      setLoadingFormulas(false);
    }
  }

  async function handleExport() {
    if (!fileMeta?.file_id || !reportResult) return;
    setExportStatus("loading");
    try {
      const savedPath = await excelExport(fileMeta.file_id, reportResult);
      setExportStatus({ path: savedPath });
    } catch (err) {
      setExportStatus({ error: toUserMessage(err) });
    }
  }

  // ── Derived state ──────────────────────────────────────────────────────────

  const hasFile = Boolean(fileMeta?.file_id);

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-5">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">엑셀 AI</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          xlsx / xls / csv 파일을 업로드하고 AI로 분석하세요
        </p>
      </div>

      {/* Dropzone */}
      <div
        {...getRootProps()}
        className={[
          "flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed p-10 text-center transition-colors",
          isDragActive
            ? "border-primary bg-primary/5"
            : "border-border hover:border-primary/50 hover:bg-muted/30",
          uploading ? "pointer-events-none opacity-60" : "",
        ].join(" ")}
        role="region"
        aria-label="파일 업로드 영역"
      >
        <input {...getInputProps()} />
        {uploading ? (
          <Spinner label="업로드 중..." />
        ) : isDragActive ? (
          <p className="text-sm font-medium text-primary">여기에 파일을 놓으세요</p>
        ) : (
          <>
            <svg
              className="mb-3 h-10 w-10 text-muted-foreground/50"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={1.5}
              stroke="currentColor"
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
              />
            </svg>
            <p className="text-sm font-medium">
              파일을 드래그하거나 클릭하여 업로드
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              지원 형식: .xlsx, .xls, .csv (최대 50MB)
            </p>
          </>
        )}
      </div>

      {uploadError && <ErrorCard message={uploadError} />}

      {/* File metadata */}
      {hasFile && (
        <FileMetaCard
          meta={fileMeta}
          activeSheet={activeSheet}
          onSheetChange={handleSheetChange}
        />
      )}

      {/* Tab navigation + content — only shown once a file is loaded */}
      {hasFile && (
        <Card>
          {/* Tab bar */}
          <div
            className="flex border-b"
            role="tablist"
            aria-label="분석 탭"
          >
            {TABS.map((tab) => {
              const hasContent =
                (tab.id === "analyze" && analyzeResult) ||
                (tab.id === "report" && reportResult) ||
                (tab.id === "formulas" && formulasResult);
              return (
                <button
                  key={tab.id}
                  role="tab"
                  aria-selected={activeTab === tab.id}
                  aria-controls={`tabpanel-${tab.id}`}
                  onClick={() => setActiveTab(tab.id)}
                  className={[
                    "flex items-center px-5 py-3 text-sm font-medium transition-colors",
                    activeTab === tab.id
                      ? "border-b-2 border-primary text-primary"
                      : "text-muted-foreground hover:text-foreground",
                  ].join(" ")}
                >
                  {tab.label}
                  {hasContent && <DotBadge />}
                </button>
              );
            })}
          </div>

          <CardContent className="space-y-4 pt-5">
            {/* ── 데이터 분석 탭 ── */}
            {activeTab === "analyze" && (
              <div
                id="tabpanel-analyze"
                role="tabpanel"
                className="space-y-4"
              >
                {/* Sample-based analysis notice */}
                <p className="rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
                  AI는 데이터의 구조와 샘플(첫 5행)을 기반으로 분석합니다.
                  정확한 합계·평균 등 통계는 엑셀 수식(SUM, AVERAGE 등)을 활용하세요.
                </p>

                <div className="flex gap-2">
                  <Textarea
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    placeholder="데이터에 대해 궁금한 점을 자연어로 질문하세요. 예: 월별 매출 트렌드는 어떻게 되나요?"
                    className="min-h-[80px] flex-1 resize-none"
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                        handleAnalyze();
                      }
                    }}
                  />
                  <Button
                    onClick={handleAnalyze}
                    disabled={analyzingData || !question.trim()}
                    className="self-end"
                  >
                    분석하기
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Cmd/Ctrl + Enter로 빠르게 실행할 수 있습니다.
                </p>

                {analyzingData && <Spinner label="AI가 데이터를 분석 중입니다..." />}
                {analyzeError && <ErrorCard message={analyzeError} />}
                {!analyzingData && !analyzeError && !analyzeResult && (
                  <EmptyState
                    icon={UploadCloud}
                    title="질문을 입력하고 '분석하기'를 눌러주세요"
                    description="데이터에 대해 자연어로 질문하면 AI가 분석 결과를 알려드립니다."
                  />
                )}
                {analyzeResult && (
                  <div className="rounded-lg border bg-muted/20 p-4">
                    <MarkdownView content={analyzeResult} />
                  </div>
                )}

                {/* Chart preview shown in the analysis tab */}
                <ChartPreview
                  chartData={chartData}
                  chartType={chartType}
                  onChartTypeChange={setChartType}
                />
              </div>
            )}

            {/* ── 리포트 생성 탭 ── */}
            {activeTab === "report" && (
              <div
                id="tabpanel-report"
                role="tabpanel"
                className="space-y-4"
              >
                <div className="flex items-center justify-between">
                  <p className="text-sm text-muted-foreground">
                    AI가 데이터를 분석하여 종합 리포트를 자동으로 생성합니다.
                  </p>
                  <Button onClick={handleGenerateReport} disabled={generatingReport}>
                    {generatingReport ? "생성 중..." : "리포트 생성"}
                  </Button>
                </div>

                {generatingReport && <Spinner label="AI 리포트를 작성 중입니다..." />}
                {reportError && <ErrorCard message={reportError} />}
                {reportResult && (
                  <>
                    <div className="rounded-lg border bg-muted/20 p-4">
                      <MarkdownView content={reportResult} />
                    </div>

                    <Separator />

                    {/* Export section */}
                    <div className="flex items-center gap-3">
                      <Button
                        variant="outline"
                        onClick={handleExport}
                        disabled={exportStatus === "loading"}
                      >
                        {exportStatus === "loading" ? "내보내기 중..." : "Excel로 내보내기"}
                      </Button>
                      {exportStatus?.path && (
                        <span className="text-xs text-green-600">
                          저장 완료: {exportStatus.path}
                        </span>
                      )}
                      {exportStatus?.error && (
                        <span className="text-xs text-destructive">
                          {exportStatus.error}
                        </span>
                      )}
                    </div>
                  </>
                )}
              </div>
            )}

            {/* ── 수식 제안 탭 ── */}
            {activeTab === "formulas" && (
              <div
                id="tabpanel-formulas"
                role="tabpanel"
                className="space-y-4"
              >
                <div className="flex items-center justify-between">
                  <p className="text-sm text-muted-foreground">
                    열 이름과 데이터 유형을 기반으로 유용한 엑셀 수식을 제안합니다.
                  </p>
                  <Button onClick={handleSuggestFormulas} disabled={loadingFormulas}>
                    {loadingFormulas ? "분석 중..." : "수식 제안 받기"}
                  </Button>
                </div>

                {loadingFormulas && <Spinner label="엑셀 수식을 분석 중입니다..." />}
                {formulasError && <ErrorCard message={formulasError} />}
                {formulasResult && (
                  <div className="rounded-lg border bg-muted/20 p-4">
                    <MarkdownView content={formulasResult} />
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
