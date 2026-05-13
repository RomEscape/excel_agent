import React, { useState, useEffect, useCallback } from "react";
import {
  Mail,
  RefreshCw,
  LogOut,
  Loader2,
  ChevronRight,
  Sparkles,
  Tags,
  Copy,
  CheckCheck,
  InboxIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import EmptyState from "@/components/ui/empty-state";
import {
  gmailStatus,
  gmailConnect,
  gmailDisconnect,
  gmailFetchEmails,
  gmailGetEmailBody,
  gmailSummarizeEmail,
  gmailSummarizeBatch,
  gmailDraftReply,
  gmailPrioritize,
} from "@/lib/api";
import { toUserMessage } from "@/lib/errorMessages";

// ── Priority badge config ────────────────────────────────────────────────────

/** Static keyword-based priority (from filter_service) */
const STATIC_PRIORITY_BADGE = {
  high: { label: "긴급", variant: "destructive" },
  medium: { label: "일반", variant: "secondary" },
  low: { label: "낮음", variant: "outline" },
};

/** AI-classified priority tags */
const AI_TAG_STYLES = {
  긴급: "bg-red-100 text-red-700 border-red-300 dark:bg-red-950/40 dark:text-red-400",
  일반: "bg-blue-100 text-blue-700 border-blue-300 dark:bg-blue-950/40 dark:text-blue-400",
  FYI: "bg-gray-100 text-gray-600 border-gray-300 dark:bg-gray-800/60 dark:text-gray-400",
};

/** Returns the inline style class for an AI priority tag. */
function AIPriorityBadge({ tag }) {
  const cls = AI_TAG_STYLES[tag] ?? AI_TAG_STYLES["일반"];
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-semibold ${cls}`}
    >
      {tag}
    </span>
  );
}

// ── EmailListItem ────────────────────────────────────────────────────────────

function EmailListItem({ email, onSelect, aiPriority }) {
  const priority = STATIC_PRIORITY_BADGE[email.priority] ?? STATIC_PRIORITY_BADGE.medium;
  return (
    <button
      onClick={() => onSelect(email)}
      className="flex w-full items-start gap-3 rounded-lg p-3 text-left transition-colors hover:bg-accent"
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium">{email.subject || "(제목 없음)"}</span>
          <div className="flex shrink-0 items-center gap-1">
            {aiPriority && <AIPriorityBadge tag={aiPriority.tag} />}
            <Badge variant={priority.variant} className="text-[10px]">
              {priority.label}
            </Badge>
          </div>
        </div>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">{email.sender}</p>
        {aiPriority?.reason && (
          <p className="mt-0.5 truncate text-xs text-muted-foreground italic">
            AI: {aiPriority.reason}
          </p>
        )}
        {email.snippet && (
          <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{email.snippet}</p>
        )}
      </div>
      <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />
    </button>
  );
}

// ── EmailDetail ──────────────────────────────────────────────────────────────

function EmailDetail({ email, onBack }) {
  const [body, setBody] = useState(email.body ?? null);
  const [bodyLoading, setBodyLoading] = useState(!email.body);
  const [bodyError, setBodyError] = useState("");

  const [summary, setSummary] = useState(null);
  const [summarizing, setSummarizing] = useState(false);
  const [summaryError, setSummaryError] = useState("");

  // AI draft reply state
  const [draft, setDraft] = useState(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [draftError, setDraftError] = useState("");
  const [copied, setCopied] = useState(false);

  // Load body on mount if not already available
  useEffect(() => {
    if (email.body) return;

    let cancelled = false;
    setBodyLoading(true);
    setBodyError("");

    gmailGetEmailBody(email.id)
      .then((result) => {
        if (cancelled) return;
        const text =
          result?.body ?? result?.content ?? (typeof result === "string" ? result : null);
        setBody(text);
      })
      .catch((err) => {
        if (cancelled) return;
        setBodyError(
          toUserMessage(err, "이메일 본문을 불러올 수 없습니다. 다시 시도해 주세요.")
        );
      })
      .finally(() => {
        if (!cancelled) setBodyLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [email.id, email.body]);

  const handleSummarize = async () => {
    setSummarizing(true);
    setSummaryError("");
    try {
      const result = await gmailSummarizeEmail(email.id);
      setSummary(result?.summary ?? result?.response ?? String(result));
    } catch (e) {
      setSummaryError(toUserMessage(e, "AI 요약에 실패했습니다. 다시 시도해 주세요."));
    } finally {
      setSummarizing(false);
    }
  };

  const handleDraftReply = async () => {
    setDraftLoading(true);
    setDraftError("");
    setDraft(null);
    try {
      const result = await gmailDraftReply(email.id);
      setDraft(result?.draft ?? result?.response ?? String(result));
    } catch (e) {
      setDraftError(toUserMessage(e, "AI 답장 초안 생성에 실패했습니다. 다시 시도해 주세요."));
    } finally {
      setDraftLoading(false);
    }
  };

  const handleCopyDraft = async () => {
    if (!draft) return;
    try {
      await navigator.clipboard.writeText(draft);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API may be unavailable in Tauri WebView — fallback silently
    }
  };

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" onClick={onBack}>
        ← 목록으로
      </Button>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{email.subject || "(제목 없음)"}</CardTitle>
          <p className="text-sm text-muted-foreground">보낸 사람: {email.sender}</p>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Body section */}
          {bodyLoading ? (
            <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              본문 불러오는 중...
            </div>
          ) : bodyError ? (
            <p className="text-sm text-destructive">{bodyError}</p>
          ) : body ? (
            <ScrollArea className="h-64 rounded border p-3">
              <pre className="whitespace-pre-wrap text-sm">{body}</pre>
            </ScrollArea>
          ) : (
            <p className="text-sm text-muted-foreground">본문이 없습니다.</p>
          )}

          {/* Action buttons */}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={handleSummarize} disabled={summarizing}>
              {summarizing ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <Sparkles className="mr-1 h-3 w-3" />
              )}
              AI 요약
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={handleDraftReply}
              disabled={draftLoading}
            >
              {draftLoading ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <Mail className="mr-1 h-3 w-3" />
              )}
              AI 답장 초안
            </Button>
          </div>

          {summaryError && <p className="text-sm text-destructive">{summaryError}</p>}

          {summary && (
            <div className="rounded-lg border bg-muted/40 p-3">
              <p className="mb-1 text-xs font-semibold uppercase text-muted-foreground">
                AI 요약
              </p>
              <p className="whitespace-pre-wrap text-sm">{summary}</p>
            </div>
          )}

          {/* AI draft reply section */}
          {draftLoading && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              AI가 답장 초안을 작성 중입니다...
            </div>
          )}

          {draftError && <p className="text-sm text-destructive">{draftError}</p>}

          {draft !== null && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold uppercase text-muted-foreground">
                  AI 답장 초안
                </p>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-2 text-xs"
                  onClick={handleCopyDraft}
                >
                  {copied ? (
                    <>
                      <CheckCheck className="mr-1 h-3 w-3 text-green-500" />
                      복사됨
                    </>
                  ) : (
                    <>
                      <Copy className="mr-1 h-3 w-3" />
                      복사
                    </>
                  )}
                </Button>
              </div>
              <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                className="min-h-[180px] resize-y text-sm"
                aria-label="AI 답장 초안 편집"
              />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ── EmailModule (main) ───────────────────────────────────────────────────────

export default function EmailModule() {
  const [connected, setConnected] = useState(null);
  const [emails, setEmails] = useState([]);
  const [selectedEmail, setSelectedEmail] = useState(null);
  const [connecting, setConnecting] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchResult, setBatchResult] = useState(null);
  const [statusMsg, setStatusMsg] = useState("");
  const [statusError, setStatusError] = useState(false);

  // AI priority analysis state
  const [prioritizing, setPrioritizing] = useState(false);
  const [priorityMap, setPriorityMap] = useState({}); // emailId -> { tag, reason }
  const [priorityError, setPriorityError] = useState("");

  const loadStatus = useCallback(async () => {
    try {
      const s = await gmailStatus();
      setConnected(s?.connected ?? false);
    } catch {
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const handleConnect = async () => {
    setConnecting(true);
    setStatusMsg("Google 인증 진행 중... 브라우저가 열립니다.");
    setStatusError(false);
    try {
      await gmailConnect();
      await loadStatus();
      setStatusMsg("연결 완료!");
    } catch (e) {
      setStatusMsg(toUserMessage(e, "Gmail 연결에 실패했습니다. 자격증명을 확인해 주세요."));
      setStatusError(true);
    } finally {
      setConnecting(false);
    }
  };

  const handleDisconnect = async () => {
    try {
      await gmailDisconnect();
      setConnected(false);
      setEmails([]);
      setPriorityMap({});
      setStatusMsg("연결 해제됨");
      setStatusError(false);
    } catch (e) {
      setStatusMsg(toUserMessage(e));
      setStatusError(true);
    }
  };

  const handleFetch = async () => {
    setFetching(true);
    setStatusError(false);
    setPriorityMap({});
    try {
      const data = await gmailFetchEmails(20);
      const list = Array.isArray(data) ? data : data?.emails ?? [];
      setEmails(list);
      setStatusMsg(`${list.length}개 메일 불러옴`);
    } catch (e) {
      setStatusMsg(toUserMessage(e, "메일을 불러오지 못했습니다. 다시 시도해 주세요."));
      setStatusError(true);
    } finally {
      setFetching(false);
    }
  };

  const handleBatchSummarize = async () => {
    setBatchLoading(true);
    setBatchResult(null);
    setStatusError(false);
    try {
      const result = await gmailSummarizeBatch(10);
      setBatchResult(result?.summary ?? result?.response ?? JSON.stringify(result, null, 2));
    } catch (e) {
      setStatusMsg(toUserMessage(e, "AI 일괄 요약에 실패했습니다. 다시 시도해 주세요."));
      setStatusError(true);
    } finally {
      setBatchLoading(false);
    }
  };

  const handlePrioritize = async () => {
    if (emails.length === 0) return;
    setPrioritizing(true);
    setPriorityError("");
    try {
      const ids = emails.map((e) => e.id);
      const result = await gmailPrioritize(ids);
      const map = {};
      for (const item of result?.priorities ?? []) {
        map[item.id] = { tag: item.tag, reason: item.reason };
      }
      setPriorityMap(map);
    } catch (e) {
      setPriorityError(
        toUserMessage(e, "우선순위 분석에 실패했습니다. 다시 시도해 주세요.")
      );
    } finally {
      setPrioritizing(false);
    }
  };

  if (selectedEmail) {
    return (
      <EmailDetail email={selectedEmail} onBack={() => setSelectedEmail(null)} />
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">메일 AI</h1>
        <p className="mt-1 text-sm text-muted-foreground">Gmail 연동 및 AI 자동 요약</p>
      </div>

      {/* Connection status */}
      <Card>
        <CardContent className="flex items-center justify-between py-4">
          <div className="flex items-center gap-3">
            <Mail className="h-5 w-5 text-primary" />
            <div>
              <p className="text-sm font-medium">Gmail 연결 상태</p>
              <p className="text-xs text-muted-foreground">
                {connected === null ? "확인 중..." : connected ? "연결됨" : "미연결"}
              </p>
            </div>
            {connected !== null && (
              <Badge variant={connected ? "success" : "secondary"}>
                {connected ? "연결됨" : "미연결"}
              </Badge>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {!connected && (
              <Button onClick={handleConnect} disabled={connecting} size="sm">
                {connecting && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                Google 계정 연결
              </Button>
            )}
            {connected && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleFetch}
                  disabled={fetching}
                >
                  {fetching ? (
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                  ) : (
                    <RefreshCw className="mr-1 h-3 w-3" />
                  )}
                  메일 불러오기
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleBatchSummarize}
                  disabled={batchLoading}
                >
                  {batchLoading ? (
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                  ) : (
                    <Sparkles className="mr-1 h-3 w-3" />
                  )}
                  AI 일괄 요약
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleDisconnect}
                >
                  <LogOut className="mr-1 h-3 w-3" />
                  연결 해제
                </Button>
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {statusMsg && (
        <p className={`text-xs ${statusError ? "text-destructive" : "text-muted-foreground"}`}>
          {statusMsg}
        </p>
      )}

      {/* Setup guide when not connected */}
      {connected === false && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">연결 전 준비 사항</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm text-muted-foreground">
            <p>1. Google Cloud Console에서 OAuth 2.0 자격증명을 생성하세요.</p>
            <p>
              2. <strong>자격증명 관리</strong>에서{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-xs">google_client_id</code>와{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-xs">google_client_secret</code>
              을 저장하세요.
            </p>
            <p>3. 위 버튼으로 Google 계정을 연결하세요.</p>
          </CardContent>
        </Card>
      )}

      {/* Batch result */}
      {batchResult && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">AI 일괄 요약 결과</CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-48">
              <pre className="whitespace-pre-wrap text-sm">{batchResult}</pre>
            </ScrollArea>
          </CardContent>
        </Card>
      )}

      {/* Email list */}
      {connected && emails.length === 0 && !fetching && (
        <EmptyState
          icon={InboxIcon}
          title="받은 메일이 없습니다"
          description="'메일 불러오기' 버튼을 눌러 받은 편지함을 가져오세요."
        />
      )}

      {emails.length > 0 && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm">받은 메일 ({emails.length})</CardTitle>
              <Button
                size="sm"
                variant="outline"
                onClick={handlePrioritize}
                disabled={prioritizing}
              >
                {prioritizing ? (
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                ) : (
                  <Tags className="mr-1 h-3 w-3" />
                )}
                우선순위 분석
              </Button>
            </div>
            {priorityError && (
              <p className="text-xs text-destructive mt-1">{priorityError}</p>
            )}
          </CardHeader>
          <CardContent className="p-2">
            <ScrollArea className="h-[400px]">
              <div className="divide-y">
                {emails.map((email) => (
                  <EmailListItem
                    key={email.id}
                    email={email}
                    onSelect={setSelectedEmail}
                    aiPriority={priorityMap[email.id] ?? null}
                  />
                ))}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
