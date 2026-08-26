import React, { useState } from "react";
import {
  Cpu,
  ChevronRight,
  ExternalLink,
  Monitor,
  Apple,
  Terminal,
  Copy,
  Check,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

/**
 * 안내 대상은 Ollama 설치 하나뿐이라 탭 바가 없다.
 *
 * 예전에는 탭이 5개였다 — 텔레그램·Slack/Discord는 메신저 봇 제거와 함께,
 * Gmail은 스킬 제거와 함께, Claude API는 LLM 경로가 Ollama 하나로 확정되며
 * 사라졌다. 탭이 다시 둘 이상이 되면 탭 바를 되살릴 것.
 */

// ── 공통 building blocks ─────────────────────────────────────────────────────

function Step({ number, title, children }) {
  return (
    <div className="flex gap-4">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-bold text-primary mt-0.5">
        {number}
      </div>
      <div className="flex-1 pb-5">
        <p className="text-sm font-semibold mb-1">{title}</p>
        <div className="text-sm text-muted-foreground space-y-1">{children}</div>
      </div>
    </div>
  );
}

function CodeBlock({ children }) {
  return (
    <code className="inline-block rounded bg-muted px-2 py-0.5 font-mono text-xs text-foreground">
      {children}
    </code>
  );
}

function Note({ children }) {
  return (
    <p className="mt-2 rounded-md border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-400">
      {children}
    </p>
  );
}

function LinkBadge({ href, children }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 rounded border border-primary/30 bg-primary/5 px-2 py-0.5 text-xs font-medium text-primary hover:bg-primary/10 transition-colors"
    >
      {children}
      <ExternalLink className="h-3 w-3" />
    </a>
  );
}

function NavButton({ onClick, label }) {
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
    >
      {label} <ChevronRight className="h-3 w-3" />
    </button>
  );
}

/**
 * 1-click 클립보드 복사 코드 블록.
 * 비개발자가 터미널 명령을 그대로 복사할 수 있도록 큰 hit area + 시각 피드백 제공.
 */
function CopyableCommand({ command, label }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // ignore — 사용자에게는 텍스트가 보이므로 수동 복사 가능
    }
  };
  return (
    <div className="mt-1.5 flex items-center gap-2 rounded-md border border-border bg-muted/60 px-3 py-2">
      <Terminal className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <code className="flex-1 select-all font-mono text-xs">{command}</code>
      <button
        type="button"
        onClick={handleCopy}
        className="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-0.5 text-[11px] font-medium hover:bg-muted"
        title={label ?? "복사"}
      >
        {copied ? (
          <>
            <Check className="h-3 w-3 text-green-600" />
            복사됨
          </>
        ) : (
          <>
            <Copy className="h-3 w-3" />
            복사
          </>
        )}
      </button>
    </div>
  );
}

// ── Ollama 가이드 (기존 유지) ────────────────────────────────────────────────

function OllamaGuide() {
  const [os, setOs] = useState("mac");

  return (
    <div>
      <p className="mb-4 text-sm text-muted-foreground">
        Ollama는 AI 모델을 인터넷 없이 내 컴퓨터에서 실행할 수 있는 도구입니다. 설치 후 앱과 자동으로 연결됩니다.
      </p>

      <div className="mb-5 flex gap-2">
        <button
          onClick={() => setOs("mac")}
          className={`flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium transition-colors ${
            os === "mac"
              ? "border-primary bg-primary/10 text-primary"
              : "border-border text-muted-foreground hover:text-foreground"
          }`}
        >
          <Apple className="h-3.5 w-3.5" /> macOS
        </button>
        <button
          onClick={() => setOs("win")}
          className={`flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium transition-colors ${
            os === "win"
              ? "border-primary bg-primary/10 text-primary"
              : "border-border text-muted-foreground hover:text-foreground"
          }`}
        >
          <Monitor className="h-3.5 w-3.5" /> Windows
        </button>
      </div>

      <div className="divide-y">
        {os === "mac" ? (
          <>
            <Step number={1} title="공식 홈페이지 접속">
              <p>Ollama 공식 다운로드 페이지에 접속합니다.</p>
              <div className="mt-2">
                <LinkBadge href="https://ollama.com/download">ollama.com/download</LinkBadge>
              </div>
            </Step>
            <Step number={2} title="다운로드">
              <p><strong>Download for Mac</strong> 버튼을 클릭해 <CodeBlock>Ollama-darwin.zip</CodeBlock> 파일을 받습니다.</p>
            </Step>
            <Step number={3} title="설치">
              <p>다운로드된 압축 파일을 풀면 Ollama 앱 아이콘이 나타납니다.</p>
              <p>아이콘을 <strong>응용 프로그램(Applications)</strong> 폴더로 드래그 앤 드롭합니다.</p>
            </Step>
            <Step number={4} title="실행">
              <p>응용 프로그램 폴더에서 Ollama를 실행합니다.</p>
              <p>"인터넷에서 다운로드된 앱" 확인 창이 뜨면 <strong>열기</strong>를 누릅니다.</p>
              <p>상단 메뉴 바에 라마 아이콘이 생기면 실행된 것입니다.</p>
            </Step>
            <Step number={5} title="모델 다운로드">
              <p>터미널을 열고 아래 명령어를 입력합니다.</p>
              <CopyableCommand command="ollama pull qwen3:4b" />
              <p className="mt-1">다운로드가 완료되면 앱에서 Ollama를 바로 사용할 수 있습니다.</p>
              <Note>다른 모델을 사용하려면 <CodeBlock>ollama pull 모델명</CodeBlock> 형식으로 입력하고, 설정에서 모델명을 변경하세요.</Note>
            </Step>
          </>
        ) : (
          <>
            <Step number={1} title="공식 홈페이지 접속">
              <p>Ollama 공식 다운로드 페이지에 접속합니다.</p>
              <div className="mt-2">
                <LinkBadge href="https://ollama.com/download">ollama.com/download</LinkBadge>
              </div>
            </Step>
            <Step number={2} title="다운로드">
              <p><strong>Download for Windows</strong> 버튼을 클릭해 <CodeBlock>OllamaSetup.exe</CodeBlock> 파일을 받습니다.</p>
            </Step>
            <Step number={3} title="설치">
              <p>다운로드한 설치 파일을 실행합니다.</p>
              <p>별도 설정 없이 <strong>Install</strong> 버튼만 누르면 자동으로 설치됩니다.</p>
            </Step>
            <Step number={4} title="실행 확인">
              <p>설치가 끝나면 Ollama가 자동으로 실행됩니다.</p>
              <p>우측 하단 <strong>시스템 트레이</strong>(작은 아이콘 모음)에 라마 아이콘이 나타나면 준비 완료입니다.</p>
            </Step>
            <Step number={5} title="모델 다운로드">
              <p>명령 프롬프트(cmd) 또는 PowerShell을 열고 아래 명령어를 입력합니다.</p>
              <CopyableCommand command="ollama pull qwen3:4b" />
              <p className="mt-1">다운로드가 완료되면 앱에서 Ollama를 바로 사용할 수 있습니다.</p>
              <Note>다른 모델을 사용하려면 <CodeBlock>ollama pull 모델명</CodeBlock> 형식으로 입력하고, 설정에서 모델명을 변경하세요.</Note>
            </Step>
          </>
        )}
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function SetupGuide() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">설치 가이드</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          로컬 AI 엔진(Ollama) 설치를 단계별로 안내해요.
        </p>
      </div>

      <Card>
        {/* Tab bar */}
        <CardContent className="pt-6">
          <OllamaGuide />
        </CardContent>
      </Card>
    </div>
  );
}
