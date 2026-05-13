import React, { useState, useEffect, useCallback, useMemo } from "react";
import { RefreshCw, ClipboardList } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import EmptyState from "@/components/ui/empty-state";
import { getAuditLogs, getCommandAuditLogs } from "@/lib/api";
import { relativeTime } from "@/lib/utils";

/** Action → badge variant mapping */
function actionVariant(action) {
  if (!action) return "secondary";
  if (action.includes("delete") || action.includes("error")) return "destructive";
  if (action.includes("llm_chat") || action.includes("ai")) return "default";
  return "secondary";
}

/**
 * 실행 기록 (구 "감사 로그").
 *
 * URL/페이지 키는 audit를 그대로 유지하지만, 사용자 표기는 "실행 기록"으로 통일.
 * Dashboard "승인 대기" 카드에서 진입할 수 있으며, 그 경우 confirm_pending 필터로 시작.
 */
export default function AuditLog() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  // window 전역 필터 — Dashboard에서 setCurrentPage("audit") 호출 직전 설정
  const initialFilter =
    (typeof window !== "undefined" && window.__privateClaw_auditFilter) || null;
  const [filter, setFilter] = useState(initialFilter);

  // 필터 사용 후 1회성 정리
  useEffect(() => {
    if (typeof window !== "undefined") window.__privateClaw_auditFilter = null;
  }, []);

  const loadLogs = useCallback(async () => {
    setLoading(true);
    try {
      // confirm_pending 필터인 경우 명령 감사 로그(grade=CONFIRM, approved=null)만 가져온다.
      if (filter === "confirm_pending") {
        const data = await getCommandAuditLogs(200, 0);
        const list = data?.logs ?? [];
        const pending = list.filter(
          (l) => (l.grade === "CONFIRM" || l.classification === "confirm") && l.approved == null
        );
        setLogs(pending);
      } else {
        const data = await getAuditLogs(100);
        const list = Array.isArray(data) ? data : data?.logs ?? [];
        // Show newest first
        setLogs([...list].reverse());
      }
    } catch {
      setLogs([]);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    loadLogs();
  }, [loadLogs]);

  const headerSubtitle = useMemo(() => {
    if (filter === "confirm_pending") {
      return "승인 대기 중인 명령만 표시 — Dashboard 카드에서 진입한 결과";
    }
    return "모든 명령/데이터 접근 내역이 로컬에 기록됩니다";
  }, [filter]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">실행 기록</h1>
          <p className="mt-1 text-sm text-muted-foreground">{headerSubtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          {filter && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setFilter(null)}
            >
              필터 해제
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={loadLogs} disabled={loading}>
            <RefreshCw className={`mr-1 h-3 w-3 ${loading ? "animate-spin" : ""}`} />
            새로고침
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">
            활동 기록{" "}
            <Badge variant="secondary" className="ml-1">
              {logs.length}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {logs.length === 0 ? (
            <EmptyState
              icon={ClipboardList}
              title={loading ? "불러오는 중..." : "기록이 없습니다."}
              description={
                filter === "confirm_pending"
                  ? "현재 승인 대기 중인 명령이 없습니다."
                  : "메신저 또는 앱에서 명령을 실행하면 여기에 기록됩니다."
              }
            />
          ) : (
            <ScrollArea className="h-[500px]">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-muted/80 backdrop-blur">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground">
                      시간
                    </th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground">
                      작업
                    </th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground">
                      대상
                    </th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground">
                      상세
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {logs.map((log) => (
                    <tr
                      key={log.id ?? `${log.timestamp ?? ""}-${log.action ?? ""}-${log.target ?? ""}`}
                      className="hover:bg-muted/30"
                    >
                      <td
                        className="px-4 py-2 text-xs text-muted-foreground whitespace-nowrap"
                        title={log.timestamp ? new Date(log.timestamp).toLocaleString("ko-KR") : undefined}
                      >
                        {log.timestamp ? relativeTime(log.timestamp) : "-"}
                      </td>
                      <td className="px-4 py-2">
                        <Badge
                          variant={actionVariant(log.action ?? log.grade)}
                          className="text-[10px]"
                        >
                          {log.action ?? log.grade ?? "-"}
                        </Badge>
                      </td>
                      <td className="px-4 py-2 text-xs text-muted-foreground">
                        {log.target ?? log.command ?? "-"}
                      </td>
                      <td className="px-4 py-2 text-xs text-muted-foreground max-w-xs truncate">
                        {log.detail ?? log.reason ?? "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollArea>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
