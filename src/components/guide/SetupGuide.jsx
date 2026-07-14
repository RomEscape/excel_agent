import React, { useState } from "react";
import {
  Mail,
  MessageCircle,
  MessagesSquare,
  Cpu,
  Bot,
  ChevronRight,
  ExternalLink,
  Monitor,
  Apple,
  Terminal,
  Copy,
  Check,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import useAppStore from "@/store/appStore";

/**
 * 탭 순서:
 *   1) Ollama 설치
 *   2) 텔레그램 봇
 *   3) Slack/Discord 봇
 *   4) Gmail 안내
 *   5) Claude API
 */
const TABS = [
  { id: "ollama",   label: "Ollama 설치",   icon: Cpu },
  { id: "telegram", label: "텔레그램 봇",   icon: MessageCircle },
  { id: "slack",    label: "Slack/Discord", icon: MessagesSquare },
  { id: "gmail",    label: "Gmail 안내",    icon: Mail },
  { id: "claude",   label: "Claude API",    icon: Bot },
];

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
              <CopyableCommand command="ollama pull skt/A.X-4.0-Light:latest" />
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
              <CopyableCommand command="ollama pull skt/A.X-4.0-Light:latest" />
              <p className="mt-1">다운로드가 완료되면 앱에서 Ollama를 바로 사용할 수 있습니다.</p>
              <Note>다른 모델을 사용하려면 <CodeBlock>ollama pull 모델명</CodeBlock> 형식으로 입력하고, 설정에서 모델명을 변경하세요.</Note>
            </Step>
          </>
        )}
      </div>
    </div>
  );
}

// ── 텔레그램 (기존 유지) ────────────────────────────────────────────────────

function TelegramGuide({ onGoToCredentials }) {
  return (
    <div>
      <p className="mb-5 text-sm text-muted-foreground">
        텔레그램 봇을 만들고 Chat ID를 등록하면 앱에서 알림을 받거나 명령을 내릴 수 있습니다.
      </p>
      <div className="divide-y">
        <Step number={1} title="BotFather에서 봇 생성">
          <p>텔레그램 앱에서 <CodeBlock>@BotFather</CodeBlock>를 검색해 채팅을 시작합니다.</p>
          <p><CodeBlock>/newbot</CodeBlock> 명령을 입력하고 안내에 따라 봇 이름과 사용자명을 입력합니다.</p>
          <p>사용자명은 반드시 <CodeBlock>bot</CodeBlock>으로 끝나야 합니다. (예: <CodeBlock>my_office_bot</CodeBlock>)</p>
        </Step>
        <Step number={2} title="Bot Token 저장">
          <p>봇 생성이 완료되면 BotFather가 <strong>토큰</strong>을 발급해줍니다.</p>
          <p>형식: <CodeBlock>1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ</CodeBlock></p>
          <p>이 토큰을 자격증명 관리의 <CodeBlock>telegram_bot_token</CodeBlock>에 저장합니다.</p>
        </Step>
        <Step number={3} title="Chat ID 확인">
          <p>텔레그램에서 <CodeBlock>@userinfobot</CodeBlock>을 검색해 채팅을 시작합니다.</p>
          <p>아무 메시지나 보내면 본인의 <strong>Chat ID</strong>를 알려줍니다.</p>
          <p>이 숫자를 자격증명 관리의 <CodeBlock>telegram_chat_id</CodeBlock>에 저장합니다.</p>
          <div className="mt-2">
            <NavButton onClick={onGoToCredentials} label="자격증명 관리로 이동" />
          </div>
        </Step>
        <Step number={4} title="봇 시작">
          <p>설정 > 메신저에서 <strong>봇 시작</strong> 버튼을 누릅니다.</p>
          <p>텔레그램 앱에서 생성한 봇에게 <CodeBlock>/start</CodeBlock>를 입력하면 연결이 완료됩니다.</p>
        </Step>
      </div>
    </div>
  );
}

// ── Slack / Discord 통합 가이드 (간단 안내) ─────────────────────────────────

function SlackDiscordGuide({ onGoToMessenger }) {
  return (
    <div>
      <p className="mb-5 text-sm text-muted-foreground">
        Slack 또는 Discord 봇을 추가하면 같은 명령을 여러 메신저에서 사용할 수 있습니다.
        설정값은 모두 <strong>설정 > 메신저</strong>에서 입력합니다.
      </p>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-md border border-border p-4">
          <div className="mb-2 flex items-center gap-2">
            <MessagesSquare className="h-4 w-4 text-[#4A154B]" />
            <p className="text-sm font-semibold">Slack 봇</p>
          </div>
          <p className="text-xs text-muted-foreground">
            Slack 워크스페이스에 봇을 추가하려면 Bot Token + App Token을 준비하세요.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <LinkBadge href="https://api.slack.com/apps">api.slack.com/apps</LinkBadge>
          </div>
        </div>

        <div className="rounded-md border border-border p-4">
          <div className="mb-2 flex items-center gap-2">
            <MessagesSquare className="h-4 w-4 text-[#5865F2]" />
            <p className="text-sm font-semibold">Discord 봇</p>
          </div>
          <p className="text-xs text-muted-foreground">
            Discord Developer Portal에서 봇을 만들고 Token을 발급받습니다.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <LinkBadge href="https://discord.com/developers/applications">discord.com/developers</LinkBadge>
          </div>
        </div>
      </div>

      <div className="mt-5">
        <NavButton onClick={onGoToMessenger} label="설정 / 메신저로 이동" />
      </div>
    </div>
  );
}

// ── Gmail 안내 ───────────────────────────────────────────────────────────────

function GmailGuide() {
  return (
    <div>
      <div className="rounded-md border border-amber-200 bg-amber-50/60 p-3 dark:border-amber-900/40 dark:bg-amber-950/30">
        <div className="flex items-start gap-2">
          <Mail className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
          <div className="text-sm">
            <p className="font-semibold text-amber-900 dark:text-amber-100">
              Gmail 연동은 메신저 봇 명령으로 처리됩니다
            </p>
            <p className="mt-1 text-xs text-amber-800 dark:text-amber-200">
              officeclaw v3.0부터 Gmail 등 외부 연동은 앱이 직접 관리하지 않고
              메신저 봇 명령으로 처리됩니다. 메신저에서 "메일 확인해줘" 명령을
              보내면 자동으로 Gmail 작업이 수행됩니다.
            </p>
            <p className="mt-2 text-xs text-amber-800 dark:text-amber-200">
              Gmail을 처음 사용할 때 OAuth 인증 페이지로 안내되며, 앱 내에서
              별도 자격증명을 등록할 필요가 없습니다.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Claude API ───────────────────────────────────────────────────────────────

function ClaudeGuide({ onGoToCredentials, onGoToSettings }) {
  return (
    <div>
      <p className="mb-5 text-sm text-muted-foreground">
        Claude API는 Anthropic에서 제공하는 클라우드 AI입니다. API 키를 발급받아 등록하면 바로 사용할 수 있습니다.
      </p>
      <div className="divide-y">
        <Step number={1} title="Anthropic Console 접속">
          <p>Anthropic Console에 접속해 계정을 만들거나 로그인합니다.</p>
          <div className="mt-2">
            <LinkBadge href="https://console.anthropic.com">console.anthropic.com</LinkBadge>
          </div>
        </Step>
        <Step number={2} title="API 키 발급">
          <p>왼쪽 메뉴에서 <strong>API Keys</strong>를 선택합니다.</p>
          <p><strong>Create Key</strong> 버튼을 클릭하고 이름을 입력합니다. (예: <CodeBlock>officeclaw</CodeBlock>)</p>
          <p>생성된 키를 복사합니다. 키는 이 화면에서 한 번만 표시되므로 바로 저장하세요.</p>
          <Note>API 키는 <CodeBlock>sk-ant-api03-</CodeBlock>로 시작합니다. 크레딧이 있어야 API를 사용할 수 있습니다.</Note>
        </Step>
        <Step number={3} title="앱에 API 키 저장">
          <p>자격증명 관리의 <CodeBlock>claude_api_key</CodeBlock>에 복사한 키를 붙여넣고 저장합니다.</p>
          <div className="mt-2">
            <NavButton onClick={onGoToCredentials} label="자격증명 관리로 이동" />
          </div>
        </Step>
        <Step number={4} title="LLM 엔진을 Claude로 변경">
          <p>설정 메뉴에서 <strong>LLM 엔진</strong>을 <strong>Claude API</strong>로 변경하고 저장합니다.</p>
          <div className="mt-2">
            <NavButton onClick={onGoToSettings} label="설정으로 이동" />
          </div>
        </Step>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function SetupGuide() {
  // 첫 진입 활성 탭 = ollama
  const [activeTab, setActiveTab] = useState("ollama");
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);

  const content = {
    ollama:   <OllamaGuide />,
    telegram: <TelegramGuide onGoToCredentials={() => setCurrentPage("credentials")} />,
    slack:    <SlackDiscordGuide onGoToMessenger={() => setCurrentPage("messenger_settings")} />,
    gmail:    <GmailGuide />,
    claude:   <ClaudeGuide
                onGoToCredentials={() => setCurrentPage("credentials")}
                onGoToSettings={() => setCurrentPage("settings")}
              />,
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">설치 가이드</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Ollama 설치부터 메신저 봇 연결까지 단계별로 안내해요.
        </p>
      </div>

      <Card>
        {/* Tab bar */}
        <div className="flex border-b overflow-x-auto">
          {TABS.map(({ id, label, icon: Icon, badge }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center justify-center gap-2 px-4 py-3 text-sm font-medium transition-colors border-b-2 -mb-px whitespace-nowrap ${
                activeTab === id
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              <span>{label}</span>
              {badge && (
                <Badge variant="default" className="ml-0.5 h-4 px-1.5 text-[10px]">
                  {badge}
                </Badge>
              )}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <CardContent className="pt-6">
          {content[activeTab]}
        </CardContent>
      </Card>
    </div>
  );
}
