/**
 * PermissionManager.jsx — Phase 3 권한 설정 UI.
 *
 * 에이전트에게 허용할 폴더, 앱, 명령어 화이트리스트를 관리한다.
 * - 허용 폴더 목록 (추가/삭제)
 * - 허용 앱 목록 체크박스 (Excel, Email, Document 등)
 * - 셸/Python 명령어 화이트리스트 (SAFE 강제 지정)
 */

import React, { useState, useEffect, useCallback } from "react";
import {
  FolderOpen,
  Plus,
  Trash2,
  Shield,
  CheckSquare,
  Square,
  RefreshCw,
  Save,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { invoke } from "@tauri-apps/api/core";
import { parseResponse } from "@/lib/api";

// ── API helpers ───────────────────────────────────────────────────────────────

async function permissionsGet() {
  const raw = await invoke("permissions_get");
  return parseResponse(raw);
}

async function permissionsUpdate(data) {
  const raw = await invoke("permissions_update", data);
  return parseResponse(raw);
}

async function permissionsWhitelistAdd(command, commandType, reason) {
  const raw = await invoke("permissions_whitelist_add", {
    command,
    command_type: commandType,
    reason: reason || "",
  });
  return parseResponse(raw);
}

async function permissionsWhitelistRemove(command) {
  const raw = await invoke("permissions_whitelist_remove", { command });
  return parseResponse(raw);
}

// ── 상수 ─────────────────────────────────────────────────────────────────────

const AVAILABLE_APPS = [
  { id: "excel", label: "엑셀 (Excel)" },
  { id: "email", label: "이메일 (Email)" },
  { id: "document", label: "문서 (Document)" },
  { id: "browser", label: "웹 브라우저" },
  { id: "terminal", label: "터미널 (읽기 전용)" },
];

// ── 컴포넌트 ──────────────────────────────────────────────────────────────────

export default function PermissionManager() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [successMsg, setSuccessMsg] = useState(null);

  // 권한 설정 상태
  const [allowedFolders, setAllowedFolders] = useState([]);
  const [allowedApps, setAllowedApps] = useState([]);
  const [shellWhitelist, setShellWhitelist] = useState([]);
  const [pythonWhitelist, setPythonWhitelist] = useState([]);

  // 입력 상태
  const [newFolder, setNewFolder] = useState("");
  const [newShellCmd, setNewShellCmd] = useState("");
  const [newPythonMod, setNewPythonMod] = useState("");

  const loadPermissions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await permissionsGet();
      setAllowedFolders(data.allowed_folders || []);
      setAllowedApps(data.allowed_apps || []);
      setShellWhitelist(data.shell_command_whitelist || []);
      setPythonWhitelist(data.python_module_whitelist || []);
    } catch (e) {
      setError(`권한 설정 로드 실패: ${e}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPermissions();
  }, [loadPermissions]);

  const savePermissions = async () => {
    setSaving(true);
    setError(null);
    setSuccessMsg(null);
    try {
      await permissionsUpdate({
        allowed_folders: allowedFolders,
        allowed_apps: allowedApps,
        shell_command_whitelist: shellWhitelist,
        python_module_whitelist: pythonWhitelist,
      });
      setSuccessMsg("권한 설정이 저장되었습니다.");
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (e) {
      setError(`저장 실패: ${e}`);
    } finally {
      setSaving(false);
    }
  };

  // 폴더 관리
  const addFolder = () => {
    const folder = newFolder.trim();
    if (!folder || allowedFolders.includes(folder)) return;
    setAllowedFolders((prev) => [...prev, folder]);
    setNewFolder("");
  };

  const removeFolder = (folder) => {
    setAllowedFolders((prev) => prev.filter((f) => f !== folder));
  };

  // 앱 토글
  const toggleApp = (appId) => {
    setAllowedApps((prev) =>
      prev.includes(appId) ? prev.filter((a) => a !== appId) : [...prev, appId]
    );
  };

  // 셸 화이트리스트
  const addShellCmd = async () => {
    const cmd = newShellCmd.trim();
    if (!cmd || shellWhitelist.includes(cmd)) return;
    try {
      await permissionsWhitelistAdd(cmd, "shell", "");
      setShellWhitelist((prev) => [...prev, cmd]);
      setNewShellCmd("");
    } catch (e) {
      setError(`화이트리스트 추가 실패: ${e}`);
    }
  };

  const removeShellCmd = async (cmd) => {
    try {
      await permissionsWhitelistRemove(cmd);
      setShellWhitelist((prev) => prev.filter((c) => c !== cmd));
    } catch (e) {
      setError(`화이트리스트 제거 실패: ${e}`);
    }
  };

  // Python 화이트리스트
  const addPythonMod = async () => {
    const mod = newPythonMod.trim();
    if (!mod || pythonWhitelist.includes(mod)) return;
    try {
      await permissionsWhitelistAdd(mod, "python", "");
      setPythonWhitelist((prev) => [...prev, mod]);
      setNewPythonMod("");
    } catch (e) {
      setError(`화이트리스트 추가 실패: ${e}`);
    }
  };

  const removePythonMod = async (mod) => {
    try {
      await permissionsWhitelistRemove(mod);
      setPythonWhitelist((prev) => prev.filter((m) => m !== mod));
    } catch (e) {
      setError(`화이트리스트 제거 실패: ${e}`);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">권한 설정</h2>
          <p className="text-sm text-muted-foreground mt-0.5">
            에이전트가 접근할 수 있는 폴더, 앱, 명령어를 관리합니다.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadPermissions}>
            <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
            새로고침
          </Button>
          <Button size="sm" onClick={savePermissions} disabled={saving}>
            <Save className="h-3.5 w-3.5 mr-1.5" />
            {saving ? "저장 중..." : "저장"}
          </Button>
        </div>
      </div>

      {/* 에러/성공 메시지 */}
      {error && (
        <div className="rounded-md bg-destructive/10 border border-destructive/30 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}
      {successMsg && (
        <div className="rounded-md bg-green-50 border border-green-200 px-4 py-3 text-sm text-green-700">
          {successMsg}
        </div>
      )}

      {/* 허용 폴더 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <FolderOpen className="h-4 w-4 text-blue-500" />
            허용 폴더
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-muted-foreground">
            에이전트가 파일을 읽고 쓸 수 있는 폴더입니다. 워크스페이스 외부 경로는 여기에 추가하면 접근이 허용됩니다.
          </p>
          <div className="flex gap-2">
            <Input
              value={newFolder}
              onChange={(e) => setNewFolder(e.target.value)}
              placeholder="~/Documents/project"
              className="text-sm"
              onKeyDown={(e) => e.key === "Enter" && addFolder()}
            />
            <Button size="sm" onClick={addFolder} disabled={!newFolder.trim()}>
              <Plus className="h-3.5 w-3.5" />
            </Button>
          </div>
          <div className="space-y-1.5">
            {allowedFolders.length === 0 ? (
              <p className="text-xs text-muted-foreground">허용된 폴더가 없습니다.</p>
            ) : (
              allowedFolders.map((folder) => (
                <div
                  key={folder}
                  className="flex items-center justify-between rounded-md bg-muted/50 px-3 py-2"
                >
                  <span className="text-sm font-mono">{folder}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
                    onClick={() => removeFolder(folder)}
                    disabled={folder === "~/PrivateClaw/Workspace"}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))
            )}
          </div>
        </CardContent>
      </Card>

      {/* 허용 앱 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <CheckSquare className="h-4 w-4 text-green-500" />
            허용 앱
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground mb-3">
            에이전트가 연동할 수 있는 앱을 선택합니다.
          </p>
          <div className="grid grid-cols-2 gap-2">
            {AVAILABLE_APPS.map(({ id, label }) => {
              const checked = allowedApps.includes(id);
              return (
                <button
                  key={id}
                  onClick={() => toggleApp(id)}
                  className={`flex items-center gap-2 rounded-md border px-3 py-2 text-sm text-left transition-colors ${
                    checked
                      ? "border-primary/50 bg-primary/5 text-foreground"
                      : "border-muted text-muted-foreground hover:border-primary/30"
                  }`}
                >
                  {checked ? (
                    <CheckSquare className="h-4 w-4 text-primary shrink-0" />
                  ) : (
                    <Square className="h-4 w-4 shrink-0" />
                  )}
                  {label}
                </button>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* 명령어 화이트리스트 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Shield className="h-4 w-4 text-orange-500" />
            명령어 화이트리스트
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-xs text-muted-foreground">
            기본적으로 CONFIRM 또는 DENIED로 분류되는 명령어를 SAFE로 강제 허용합니다.
            주의: 잘못된 화이트리스트 등록은 보안 취약점이 될 수 있습니다.
          </p>

          {/* 셸 명령어 */}
          <div className="space-y-2">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
              셸 명령어
            </p>
            <div className="flex gap-2">
              <Input
                value={newShellCmd}
                onChange={(e) => setNewShellCmd(e.target.value)}
                placeholder="rm ~/PrivateClaw/Workspace/temp.txt"
                className="text-sm font-mono"
                onKeyDown={(e) => e.key === "Enter" && addShellCmd()}
              />
              <Button size="sm" onClick={addShellCmd} disabled={!newShellCmd.trim()}>
                <Plus className="h-3.5 w-3.5" />
              </Button>
            </div>
            {shellWhitelist.length === 0 ? (
              <p className="text-xs text-muted-foreground">등록된 셸 명령어가 없습니다.</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {shellWhitelist.map((cmd) => (
                  <Badge key={cmd} variant="secondary" className="gap-1 pr-1 font-mono text-xs">
                    {cmd}
                    <button
                      onClick={() => removeShellCmd(cmd)}
                      className="ml-0.5 hover:text-destructive"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </Badge>
                ))}
              </div>
            )}
          </div>

          {/* Python 모듈 */}
          <div className="space-y-2">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
              Python 모듈
            </p>
            <div className="flex gap-2">
              <Input
                value={newPythonMod}
                onChange={(e) => setNewPythonMod(e.target.value)}
                placeholder="pandas, numpy"
                className="text-sm font-mono"
                onKeyDown={(e) => e.key === "Enter" && addPythonMod()}
              />
              <Button size="sm" onClick={addPythonMod} disabled={!newPythonMod.trim()}>
                <Plus className="h-3.5 w-3.5" />
              </Button>
            </div>
            {pythonWhitelist.length === 0 ? (
              <p className="text-xs text-muted-foreground">등록된 Python 모듈이 없습니다.</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {pythonWhitelist.map((mod) => (
                  <Badge key={mod} variant="secondary" className="gap-1 pr-1 font-mono text-xs">
                    {mod}
                    <button
                      onClick={() => removePythonMod(mod)}
                      className="ml-0.5 hover:text-destructive"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </Badge>
                ))}
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
