import React, { useState, useEffect, useCallback } from "react";
import { Shield, Eye, EyeOff, Save, Trash2, CheckCircle2, Mail, MessageCircle, Cpu } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { AlertDialog } from "@/components/ui/dialog";
import { Toast } from "@/components/ui/toast";
import useToast from "@/hooks/useToast";
import { listCredentials, storeCredential, deleteCredential } from "@/lib/api";
import { toUserMessage } from "@/lib/errorMessages";

/**
 * Predefined credential groups shown to the user.
 * `key` must match what the Python sidecar reads from keyring.
 */
const CREDENTIAL_GROUPS = [
  {
    id: "google",
    icon: Mail,
    title: "Google Gmail",
    description: "Gmail 연동을 위한 OAuth 앱 자격증명입니다. Google Cloud Console에서 발급하세요.",
    guideUrl: "https://console.cloud.google.com/apis/credentials",
    fields: [
      {
        key: "google_client_id",
        label: "Client ID",
        placeholder: "1234567890-abcdefg.apps.googleusercontent.com",
        sensitive: false,
        hint: "Google Cloud Console → 사용자 인증 정보 → OAuth 2.0 클라이언트 ID",
      },
      {
        key: "google_client_secret",
        label: "Client Secret",
        placeholder: "GOCSPX-...",
        sensitive: true,
        hint: "Client ID와 함께 발급되는 비밀 키입니다.",
      },
    ],
  },
  {
    id: "claude",
    icon: Cpu,
    title: "Claude API",
    description: "Anthropic Claude API를 사용하는 경우 API 키를 입력하세요. LLM 설정에서 Claude를 선택해야 작동합니다.",
    fields: [
      {
        key: "claude_api_key",
        label: "API Key",
        placeholder: "sk-ant-api03-...",
        sensitive: true,
        hint: "Anthropic Console (console.anthropic.com) → API Keys에서 발급받습니다.",
      },
    ],
  },
];

/** Single credential field row with save/delete */
function CredentialField({ fieldDef, saved, onSave, onDelete }) {
  const [value, setValue] = useState("");
  const [showValue, setShowValue] = useState(false);
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (!value.trim()) return;
    setSaving(true);
    try {
      await onSave(fieldDef.key, value.trim());
      setValue("");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <Label htmlFor={fieldDef.key}>{fieldDef.label}</Label>
        {saved && (
          <Badge variant="secondary" className="flex items-center gap-1 px-1.5 py-0 text-[10px] text-green-600 bg-green-100 dark:bg-green-950 dark:text-green-400">
            <CheckCircle2 className="h-2.5 w-2.5" />
            저장됨
          </Badge>
        )}
      </div>
      <p className="text-xs text-muted-foreground">{fieldDef.hint}</p>
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Input
            id={fieldDef.key}
            type={fieldDef.sensitive && !showValue ? "password" : "text"}
            placeholder={saved ? "••••••••  (변경하려면 새 값 입력)" : fieldDef.placeholder}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoComplete="new-password"
            className="pr-8"
          />
          {fieldDef.sensitive && (
            <button
              type="button"
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              onClick={() => setShowValue((v) => !v)}
              tabIndex={-1}
            >
              {showValue ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
            </button>
          )}
        </div>
        <Button
          size="sm"
          disabled={saving || !value.trim()}
          onClick={handleSave}
          className="shrink-0"
        >
          <Save className="mr-1 h-3 w-3" />
          {saving ? "저장 중..." : "저장"}
        </Button>
        {saved && (
          <Button
            size="sm"
            variant="ghost"
            className="shrink-0 text-muted-foreground hover:text-destructive"
            onClick={() => onDelete(fieldDef.key)}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    </div>
  );
}

export default function CredentialsManager() {
  const [savedKeys, setSavedKeys] = useState(new Set());
  // 토스트 상태 + 자동 dismiss는 useToast 훅이 소유 (Toast primitive 기본 4000ms)
  const { toast, showToast, dismissToast } = useToast();
  const [pendingDelete, setPendingDelete] = useState(null);

  const loadCredentials = useCallback(async () => {
    try {
      const data = await listCredentials();
      const list = Array.isArray(data) ? data : Array.isArray(data?.keys) ? data.keys : [];
      setSavedKeys(new Set(list));
    } catch {
      setSavedKeys(new Set());
    }
  }, []);

  useEffect(() => {
    loadCredentials();
  }, [loadCredentials]);

  const handleSave = async (key, value) => {
    try {
      await storeCredential(key, value);
      setSavedKeys((prev) => new Set([...prev, key]));
      showToast({ message: `'${key}' 저장 완료`, variant: "success" });
    } catch (err) {
      showToast({ message: toUserMessage(err, "저장에 실패했습니다."), variant: "error" });
      throw err;
    }
  };

  const handleDelete = (key) => {
    setPendingDelete(key);
  };

  const confirmDelete = async () => {
    const key = pendingDelete;
    setPendingDelete(null);
    try {
      await deleteCredential(key);
      setSavedKeys((prev) => { const next = new Set(prev); next.delete(key); return next; });
      showToast({ message: `'${key}' 삭제됨`, variant: "default" });
    } catch (err) {
      showToast({ message: toUserMessage(err, "삭제에 실패했습니다."), variant: "error" });
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">자격증명 관리</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          API 키와 토큰을 OS 보안 저장소에 안전하게 보관합니다
        </p>
      </div>

      {/* Security note */}
      <Card className="border-green-200 bg-green-50/40 dark:border-green-900/30 dark:bg-green-950/20">
        <CardContent className="flex items-center gap-3 py-3">
          <Shield className="h-4 w-4 shrink-0 text-green-600" />
          <p className="text-xs text-green-700 dark:text-green-400">
            Windows Credential Manager / macOS Keychain에 암호화되어 저장됩니다. 평문으로 디스크에 기록되지 않습니다.
          </p>
        </CardContent>
      </Card>

      {/* Credential groups */}
      {CREDENTIAL_GROUPS.map((group) => {
        const GroupIcon = group.icon;
        const savedCount = group.fields.filter((f) => savedKeys.has(f.key)).length;
        return (
          <Card key={group.id}>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <GroupIcon className="h-4 w-4 text-primary" />
                  <CardTitle className="text-base">{group.title}</CardTitle>
                </div>
                <Badge variant={savedCount === group.fields.length ? "secondary" : "outline"} className="text-xs">
                  {savedCount} / {group.fields.length} 저장됨
                </Badge>
              </div>
              <CardDescription>{group.description}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              {group.fields.map((field) => (
                <CredentialField
                  key={field.key}
                  fieldDef={field}
                  saved={savedKeys.has(field.key)}
                  onSave={handleSave}
                  onDelete={handleDelete}
                />
              ))}
            </CardContent>
          </Card>
        );
      })}

      <AlertDialog
        open={!!pendingDelete}
        title="자격증명 삭제"
        description={`'${pendingDelete}'를 삭제하시겠습니까? 삭제 후에는 복구할 수 없습니다.`}
        confirmLabel="삭제"
        confirmVariant="destructive"
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />

      <Toast toast={toast} onDismiss={dismissToast} />
    </div>
  );
}
