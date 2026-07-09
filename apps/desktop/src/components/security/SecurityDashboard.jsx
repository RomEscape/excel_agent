/**
 * 보안 대시보드 — 명령 분석 통계, 마스킹 통계, 차단 이력, 스킬 화이트리스트 관리.
 *
 * 섹션:
 *  0. 명령 분석 통계 (Phase 2) — SAFE/CONFIRM/DENIED 등급별 건수 + 최근 명령 이력
 *  1. 마스킹 설정 토글
 *  2. 마스킹 통계 — 오늘/이번주/전체 민감 데이터 마스킹 건수
 *  3. 차단 이력 — 최근 DENIED 키워드 차단 및 승인 거부 이벤트
 *  4. 스킬 화이트리스트 — 스킬별 권한(SAFE/CONFIRM/DENIED) 수정
 */

import React, { useState, useEffect, useCallback } from "react";
import { ShieldCheck, RefreshCw, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  securityStats,
  securityBlockedLog,
  securityGetWhitelist,
  securityUpdateWhitelist,
  securityGetMaskingSettings,
  securityUpdateMaskingSettings,
  getCommandAuditStats,
  getCommandAuditLogs,
  clearCommandAuditLogs,
} from "@/lib/api";
import { relativeTime } from "@/lib/utils";

// ── 헬퍼 ────────────────────────────────────────────────────────────────────

function formatTimestamp(isoStr) {
  if (!isoStr) return "-";
  try {
    const d = new Date(isoStr);
    return d.toLocaleString("ko-KR", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return isoStr;
  }
}

function blockActionLabel(action) {
  switch (action) {
    case "agent.chat.denied":
      return "차단 키워드";
    case "approval.rejected":
      return "사용자 거부";
    case "approval.auto_rejected":
      return "자동 거부";
    case "masking.blocked":
      return "민감 데이터 차단";
    default:
      return action ?? "-";
  }
}

/**
 * 스킬 이름에서 "DENIED." 내부 접두사를 제거하고 표시용 이름으로 변환한다.
 * 예: "DENIED.file_delete" → "[거부] 파일 삭제"
 */
const DENIED_DISPLAY_NAMES = {
  "DENIED.file_delete": "[거부] 파일 삭제",
  "DENIED.shell_execute": "[거부] 셸 명령 실행",
  "DENIED.system_modify": "[거부] 시스템 설정 변경",
};

function skillDisplayName(name) {
  if (!name) return "-";
  if (DENIED_DISPLAY_NAMES[name]) return DENIED_DISPLAY_NAMES[name];
  if (name.startsWith("DENIED.")) {
    const bare = name.slice(7).replace(/_/g, " ");
    return `[거부] ${bare}`;
  }
  return name;
}

function permissionBadgeVariant(level) {
  switch (level) {
    case "safe":
      return "secondary";
    case "confirm":
      return "default";
    case "denied":
      return "destructive";
    default:
      return "outline";
  }
}

function permissionLabel(level) {
  switch (level) {
    case "safe":
      return "안전";
    case "confirm":
      return "확인 필요";
    case "denied":
      return "거부";
    default:
      return level;
  }
}

// ── 섹션 0 (Phase 2): 명령 분석 통계 ──────────────────────────────────────────

/** 등급 뱃지 색상 */
function gradeBadgeClass(grade) {
  switch (grade) {
    case "SAFE":
      return "bg-blue-100 text-blue-700 border-blue-200";
    case "CONFIRM":
      return "bg-orange-100 text-orange-700 border-orange-200";
    case "DENIED":
      return "bg-red-100 text-red-700 border-red-200";
    default:
      return "bg-muted text-muted-foreground border-border";
  }
}

/** 등급 한국어 레이블 */
function gradeLabel(grade, approved) {
  if (grade === "SAFE") return "자동 실행";
  if (grade === "DENIED") return "차단됨";
  if (grade === "CONFIRM") {
    if (approved === 1) return "승인됨";
    if (approved === 0) return "거부됨";
    return "대기 중";
  }
  return grade ?? "-";
}

function gradeStatusClass(grade, approved) {
  if (grade === "SAFE") return "bg-blue-100 text-blue-700";
  if (grade === "DENIED") return "bg-red-100 text-red-700";
  if (grade === "CONFIRM") {
    if (approved === 1) return "bg-green-100 text-green-700";
    if (approved === 0) return "bg-gray-100 text-gray-700";
    return "bg-orange-100 text-orange-700";
  }
  return "bg-muted text-muted-foreground";
}

function CommandAuditSection({ stats, logs, loading, onClear }) {
  const [clearing, setClearing] = React.useState(false);
  const [clearConfirm, setClearConfirm] = React.useState(false);

  const handleClear = async () => {
    if (!clearConfirm) {
      setClearConfirm(true);
      return;
    }
    setClearing(true);
    try {
      await onClear();
    } finally {
      setClearing(false);
      setClearConfirm(false);
    }
  };

  const safeCount = stats?.safe ?? 0;
  const confirmCount = stats?.confirm ?? 0;
  const deniedCount = stats?.denied ?? 0;
  const totalCount = stats?.total ?? 0;

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">명령 분석 통계 (Phase 2)</CardTitle>
          <button
            onClick={handleClear}
            disabled={clearing || totalCount === 0}
            className="text-xs text-muted-foreground hover:text-destructive transition-colors disabled:opacity-40"
          >
            {clearing ? "초기화 중..." : clearConfirm ? "정말 초기화?" : "로그 초기화"}
          </button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <p className="text-sm text-muted-foreground">불러오는 중...</p>
        ) : (
          <>
            {/* 등급별 통계 카드 3개 */}
            <div className="grid grid-cols-3 gap-3">
              {[
                {
                  label: "SAFE",
                  labelKo: "자동 실행",
                  count: safeCount,
                  colorClass: "bg-blue-50 border-blue-200",
                  textClass: "text-blue-700",
                },
                {
                  label: "CONFIRM",
                  labelKo: "승인 요청",
                  count: confirmCount,
                  extra: stats
                    ? `(승인 ${stats.confirm_approved ?? 0} / 거부 ${stats.confirm_rejected ?? 0})`
                    : "",
                  colorClass: "bg-orange-50 border-orange-200",
                  textClass: "text-orange-700",
                },
                {
                  label: "DENIED",
                  labelKo: "차단됨",
                  count: deniedCount,
                  colorClass: "bg-red-50 border-red-200",
                  textClass: "text-red-700",
                },
              ].map(({ label, labelKo, count, extra, colorClass, textClass }) => (
                <div
                  key={label}
                  className={`rounded-md border p-3 text-center ${colorClass}`}
                >
                  <p className={`text-xs font-semibold ${textClass}`}>{label}</p>
                  <p className={`mt-1 text-2xl font-bold ${textClass}`}>{count}</p>
                  <p className="text-xs text-muted-foreground">{labelKo}</p>
                  {extra && (
                    <p className="mt-0.5 text-[10px] text-muted-foreground">{extra}</p>
                  )}
                </div>
              ))}
            </div>

            {/* 전체 건수 */}
            <p className="text-xs text-muted-foreground text-right">
              전체 {totalCount}건 분석됨
            </p>

            {/* 최근 명령 이력 테이블 */}
            {logs.length > 0 && (
              <div>
                <p className="mb-2 text-xs font-medium text-muted-foreground">최근 명령 이력</p>
                <ScrollArea className="h-52">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left text-xs text-muted-foreground">
                        <th className="pb-2 pr-3 font-medium">시각</th>
                        <th className="pb-2 pr-3 font-medium">등급</th>
                        <th className="pb-2 pr-3 font-medium">언어</th>
                        <th className="pb-2 font-medium">명령 요약 / 사유</th>
                      </tr>
                    </thead>
                    <tbody>
                      {logs.map((log) => (
                        <tr key={log.id} className="border-b last:border-0">
                          <td className="py-1.5 pr-3 tabular-nums text-muted-foreground whitespace-nowrap text-xs">
                            {relativeTime(log.timestamp)}
                          </td>
                          <td className="py-1.5 pr-3 whitespace-nowrap">
                            <span
                              className={`rounded px-1.5 py-0.5 text-[10px] font-medium border ${gradeStatusClass(log.grade, log.approved)}`}
                            >
                              {gradeLabel(log.grade, log.approved)}
                            </span>
                          </td>
                          <td className="py-1.5 pr-3 text-xs text-muted-foreground whitespace-nowrap">
                            {log.lang || "-"}
                          </td>
                          <td className="py-1.5 max-w-[200px]">
                            <p
                              className="truncate text-xs text-muted-foreground"
                              title={log.reason}
                            >
                              {log.reason || (log.command ? log.command.slice(0, 60) : "-")}
                            </p>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </ScrollArea>
              </div>
            )}

            {totalCount === 0 && (
              <p className="text-sm text-muted-foreground">아직 분석된 명령이 없습니다.</p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── 섹션 1: 마스킹 설정 토글 ─────────────────────────────────────────────────

function MaskingSettingsSection({ settings, loading, onSave }) {
  const [maskEmail, setMaskEmail] = useState(false);
  const [maskPhone, setMaskPhone] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState(null);

  // settings가 로드되면 로컬 상태 초기화
  useEffect(() => {
    if (settings) {
      setMaskEmail(settings.mask_email ?? false);
      setMaskPhone(settings.mask_phone ?? false);
    }
  }, [settings]);

  const hasChanges = settings
    ? maskEmail !== (settings.mask_email ?? false) || maskPhone !== (settings.mask_phone ?? false)
    : false;

  const handleSave = async () => {
    setSaving(true);
    setSaveResult(null);
    try {
      await onSave({ mask_email: maskEmail, mask_phone: maskPhone });
      setSaveResult({ ok: true, message: "저장되었습니다." });
    } catch (err) {
      setSaveResult({ ok: false, message: String(err) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">마스킹 대상 설정</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {loading ? (
          <p className="text-sm text-muted-foreground">불러오는 중...</p>
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              주민등록번호, 카드번호, 여권번호, 계좌번호는 항상 마스킹됩니다.
              아래 항목은 업무 맥락에서 의도적으로 사용될 수 있어 기본 OFF입니다.
            </p>
            <div className="space-y-2">
              <label className="flex items-center gap-3 rounded-md border p-3 cursor-pointer hover:bg-muted/30">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-input accent-primary"
                  checked={maskEmail}
                  onChange={(e) => { setMaskEmail(e.target.checked); setSaveResult(null); }}
                />
                <div className="flex-1">
                  <p className="text-sm font-medium">이메일 주소도 마스킹</p>
                  <p className="text-xs text-muted-foreground">
                    이메일 주소가 포함된 메시지를 AI에 전달하기 전에 자동 마스킹합니다.
                  </p>
                </div>
              </label>
              <label className="flex items-center gap-3 rounded-md border p-3 cursor-pointer hover:bg-muted/30">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-input accent-primary"
                  checked={maskPhone}
                  onChange={(e) => { setMaskPhone(e.target.checked); setSaveResult(null); }}
                />
                <div className="flex-1">
                  <p className="text-sm font-medium">전화번호도 마스킹</p>
                  <p className="text-xs text-muted-foreground">
                    한국 휴대폰 번호(010 등)가 포함된 메시지를 AI에 전달하기 전에 자동 마스킹합니다.
                  </p>
                </div>
              </label>
            </div>
            <div className="flex items-center justify-between pt-1">
              {saveResult && (
                <p className={saveResult.ok ? "text-xs text-green-600" : "text-xs text-destructive"}>
                  {saveResult.message}
                </p>
              )}
              <div className="ml-auto">
                <Button size="sm" onClick={handleSave} disabled={!hasChanges || saving}>
                  {saving ? "저장 중..." : "저장"}
                </Button>
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── 섹션 1: 마스킹 통계 ───────────────────────────────────────────────────────

function MaskingStatsSection({ stats, loading }) {
  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">마스킹 통계</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">불러오는 중...</p>
        </CardContent>
      </Card>
    );
  }

  const todayStats = stats?.masking?.today ?? {};
  const weekStats = stats?.masking?.week ?? {};
  const totalStats = stats?.masking?.total ?? {};
  const blockedCount = stats?.blocked_count ?? {};

  const todayTotal = Object.values(todayStats).reduce((a, b) => a + b, 0);
  const weekTotal = Object.values(weekStats).reduce((a, b) => a + b, 0);
  const totalTotal = Object.values(totalStats).reduce((a, b) => a + b, 0);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">민감 데이터 마스킹 통계</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* 요약 수치 */}
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: "오늘", count: todayTotal, blocked: blockedCount.today ?? 0 },
            { label: "이번 주", count: weekTotal, blocked: blockedCount.week ?? 0 },
            { label: "전체", count: totalTotal, blocked: blockedCount.total ?? 0 },
          ].map(({ label, count, blocked }) => (
            <div key={label} className="rounded-md bg-muted/50 p-3 text-center">
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className="mt-1 text-2xl font-bold text-foreground">{count}</p>
              <p className="text-xs text-muted-foreground">마스킹</p>
              <p className="mt-1 text-sm font-medium text-destructive">{blocked}</p>
              <p className="text-xs text-muted-foreground">차단</p>
            </div>
          ))}
        </div>

        {/* 유형별 상세 (오늘) */}
        {Object.keys(todayStats).length > 0 && (
          <div>
            <p className="mb-2 text-xs font-medium text-muted-foreground">오늘 마스킹된 유형</p>
            <div className="space-y-1">
              {Object.entries(todayStats).map(([type, count]) => (
                <div key={type} className="flex items-center justify-between text-sm">
                  <span className="text-foreground">{type}</span>
                  <Badge variant="secondary">{count}건</Badge>
                </div>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              총 {todayTotal}건의 민감 데이터가 LLM에 전달되기 전에 보호되었습니다.
            </p>
          </div>
        )}

        {todayTotal === 0 && (
          <p className="text-sm text-muted-foreground">오늘 마스킹된 데이터가 없습니다.</p>
        )}
      </CardContent>
    </Card>
  );
}

// ── 섹션 2: 차단 이력 ────────────────────────────────────────────────────────

function BlockedLogSection({ logs, loading }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">최근 보안 차단 내역</CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <p className="text-sm text-muted-foreground">불러오는 중...</p>
        ) : logs.length === 0 ? (
          <p className="text-sm text-muted-foreground">차단 기록이 없습니다.</p>
        ) : (
          <ScrollArea className="h-48">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="pb-2 pr-4 font-medium">시각</th>
                  <th className="pb-2 pr-4 font-medium">유형</th>
                  <th className="pb-2 font-medium">내용</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log, idx) => (
                  <tr key={idx} className="border-b last:border-0">
                    <td
                      className="py-1.5 pr-4 tabular-nums text-muted-foreground whitespace-nowrap"
                      title={formatTimestamp(log.timestamp)}
                    >
                      {relativeTime(log.timestamp)}
                    </td>
                    <td className="py-1.5 pr-4 whitespace-nowrap">
                      <Badge
                        variant={
                          log.action === "agent.chat.denied" ? "destructive" : "secondary"
                        }
                        className="text-xs"
                      >
                        {blockActionLabel(log.action)}
                      </Badge>
                    </td>
                    <td className="py-1.5 truncate max-w-[200px] text-muted-foreground">
                      {log.detail ? log.detail.slice(0, 80) : log.target ?? "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  );
}

// ── 섹션 3: 화이트리스트 관리 ────────────────────────────────────────────────

const PERMISSION_OPTIONS = [
  { value: "safe", label: "안전 (자동 실행)" },
  { value: "confirm", label: "확인 필요" },
  { value: "denied", label: "거부 (항상 차단)" },
];

function WhitelistSection({ skills, loading, onSave }) {
  const [localOverrides, setLocalOverrides] = useState({});
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState(null);

  // skills가 바뀔 때 로컬 상태 초기화
  useEffect(() => {
    const init = {};
    skills.forEach((s) => {
      init[s.name] = s.current_permission;
    });
    setLocalOverrides(init);
  }, [skills]);

  const hasChanges = skills.some(
    (s) => localOverrides[s.name] && localOverrides[s.name] !== s.current_permission
  );

  const handleChange = (skillName, value) => {
    setLocalOverrides((prev) => ({ ...prev, [skillName]: value }));
    setSaveResult(null);
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveResult(null);
    try {
      await onSave(localOverrides);
      setSaveResult({ ok: true, message: "저장되었습니다." });
    } catch (err) {
      setSaveResult({ ok: false, message: String(err) });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">명령 등급 정책</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">불러오는 중...</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">명령 등급 정책</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          각 스킬의 실행 권한을 변경할 수 있습니다. 변경 후 저장 버튼을 눌러야 적용됩니다.
        </p>

        <div className="space-y-2">
          {skills.map((skill) => {
            const currentVal = localOverrides[skill.name] ?? skill.current_permission;
            const isChanged = currentVal !== skill.default_permission;
            return (
              <div
                key={skill.name}
                className="flex items-center gap-3 rounded-md border p-3"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-foreground truncate">
                    {skill.display_name || skill.name}
                  </p>
                  <p className="text-xs text-muted-foreground truncate">{skill.name}</p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {isChanged && (
                    <Badge variant="outline" className="text-xs">
                      변경됨
                    </Badge>
                  )}
                  <Badge variant={permissionBadgeVariant(skill.default_permission)} className="text-xs">
                    기본: {permissionLabel(skill.default_permission)}
                  </Badge>
                  <select
                    className="rounded border border-input bg-background px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
                    value={currentVal}
                    onChange={(e) => handleChange(skill.name, e.target.value)}
                  >
                    {PERMISSION_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            );
          })}
        </div>

        {skills.length === 0 && (
          <p className="text-sm text-muted-foreground">등록된 스킬이 없습니다.</p>
        )}

        <div className="flex items-center justify-between pt-2">
          {saveResult && (
            <p
              className={
                saveResult.ok
                  ? "text-xs text-green-600"
                  : "text-xs text-destructive"
              }
            >
              {saveResult.message}
            </p>
          )}
          <div className="ml-auto">
            <Button
              size="sm"
              onClick={handleSave}
              disabled={!hasChanges || saving}
            >
              <Save className="mr-1.5 h-3.5 w-3.5" />
              {saving ? "저장 중..." : "저장"}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── 메인 컴포넌트 ────────────────────────────────────────────────────────────

export default function SecurityDashboard() {
  const [stats, setStats] = useState(null);
  const [blockedLogs, setBlockedLogs] = useState([]);
  const [skills, setSkills] = useState([]);
  const [maskingSettings, setMaskingSettings] = useState(null);
  // Phase 2: 명령 감사 로그 상태
  const [cmdStats, setCmdStats] = useState(null);
  const [cmdLogs, setCmdLogs] = useState([]);
  const [loading, setLoading] = useState({
    stats: false,
    logs: false,
    whitelist: false,
    masking: false,
    cmdStats: false,
    cmdLogs: false,
  });
  const [lastRefreshed, setLastRefreshed] = useState(null);

  const loadAll = useCallback(async () => {
    setLoading({
      stats: true,
      logs: true,
      whitelist: true,
      masking: true,
      cmdStats: true,
      cmdLogs: true,
    });

    const [statsRes, logsRes, wlRes, maskRes, cmdStatsRes, cmdLogsRes] =
      await Promise.allSettled([
        securityStats(),
        securityBlockedLog(50),
        securityGetWhitelist(),
        securityGetMaskingSettings(),
        getCommandAuditStats(),
        getCommandAuditLogs(30, 0),
      ]);

    if (statsRes.status === "fulfilled") setStats(statsRes.value);
    if (logsRes.status === "fulfilled") setBlockedLogs(logsRes.value?.logs ?? []);
    if (wlRes.status === "fulfilled") setSkills(wlRes.value?.skills ?? []);
    if (maskRes.status === "fulfilled") setMaskingSettings(maskRes.value);
    if (cmdStatsRes.status === "fulfilled") setCmdStats(cmdStatsRes.value);
    if (cmdLogsRes.status === "fulfilled") setCmdLogs(cmdLogsRes.value?.logs ?? []);

    setLoading({
      stats: false,
      logs: false,
      whitelist: false,
      masking: false,
      cmdStats: false,
      cmdLogs: false,
    });
    setLastRefreshed(new Date());
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleSaveWhitelist = async (overrides) => {
    await securityUpdateWhitelist(overrides);
    const wlRes = await securityGetWhitelist();
    setSkills(wlRes?.skills ?? []);
  };

  const handleSaveMaskingSettings = async (settings) => {
    const res = await securityUpdateMaskingSettings(settings);
    setMaskingSettings({ mask_email: res.mask_email, mask_phone: res.mask_phone });
  };

  const handleClearCommandAudit = async () => {
    await clearCommandAuditLogs();
    setCmdStats(null);
    setCmdLogs([]);
  };

  const isLoading = loading.stats || loading.logs || loading.whitelist || loading.masking
    || loading.cmdStats || loading.cmdLogs;

  return (
    <div className="space-y-6">
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-primary" />
          <h1 className="text-lg font-semibold">보안 대시보드</h1>
        </div>
        <div className="flex items-center gap-3">
          {lastRefreshed && (
            <p className="text-xs text-muted-foreground">
              마지막 갱신:{" "}
              {lastRefreshed.toLocaleTimeString("ko-KR", {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              })}
            </p>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={loadAll}
            disabled={isLoading}
          >
            <RefreshCw
              className={`mr-1.5 h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`}
            />
            새로고침
          </Button>
        </div>
      </div>

      {/* 섹션 0 (Phase 2): 명령 분석 통계 */}
      <CommandAuditSection
        stats={cmdStats}
        logs={cmdLogs}
        loading={loading.cmdStats || loading.cmdLogs}
        onClear={handleClearCommandAudit}
      />

      {/* 섹션 1: 마스킹 설정 토글 */}
      <MaskingSettingsSection
        settings={maskingSettings}
        loading={loading.masking}
        onSave={handleSaveMaskingSettings}
      />

      {/* 섹션 2: 마스킹 통계 */}
      <MaskingStatsSection stats={stats} loading={loading.stats} />

      {/* 섹션 3: 차단 이력 */}
      <BlockedLogSection logs={blockedLogs} loading={loading.logs} />

      {/* 섹션 4: 화이트리스트 */}
      <WhitelistSection
        skills={skills}
        loading={loading.whitelist}
        onSave={handleSaveWhitelist}
      />
    </div>
  );
}
