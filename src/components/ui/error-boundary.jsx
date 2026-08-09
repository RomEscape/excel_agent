/**
 * error-boundary.jsx — 렌더 중 예외를 화면에 표시하는 공용 UI primitive.
 *
 * Tauri WebView에는 주소창도 콘솔도 없어서, 예외가 나면 흰 화면만 남고
 * 원인을 알 수 없다. 이 바운더리는 예외 메시지와 스택을 그대로 노출해
 * 재현 없이도 원인을 파악할 수 있게 한다.
 *
 * 사용:
 *   <ErrorBoundary><App /></ErrorBoundary>
 */
import React from "react";

function ErrorReport({ title, error, info }) {
  const stack = String(error?.stack || error || "");
  const componentStack = String(info?.componentStack || "");
  const text = [title, stack, componentStack].filter(Boolean).join("\n\n");

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 99999,
        overflow: "auto",
        padding: "24px",
        background: "#1a1a1a",
        color: "#f5f5f5",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        fontSize: "13px",
        lineHeight: 1.6,
      }}
    >
      <h1 style={{ fontSize: "18px", marginBottom: "8px", color: "#ff6b6b" }}>
        {title}
      </h1>
      <p style={{ marginBottom: "16px", color: "#aaa" }}>
        아래 내용을 그대로 복사해 개발자에게 전달하세요.
      </p>
      <button
        type="button"
        onClick={() => navigator.clipboard?.writeText(text)}
        style={{
          marginBottom: "16px",
          padding: "6px 12px",
          background: "#333",
          color: "#f5f5f5",
          border: "1px solid #555",
          borderRadius: "4px",
          cursor: "pointer",
        }}
      >
        오류 내용 복사
      </button>
      <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", margin: 0 }}>
        {stack}
      </pre>
      {componentStack && (
        <>
          <h2 style={{ fontSize: "14px", margin: "20px 0 8px", color: "#ffa94d" }}>
            컴포넌트 스택
          </h2>
          <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", margin: 0 }}>
            {componentStack}
          </pre>
        </>
      )}
    </div>
  );
}

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null, info: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    this.setState({ error, info });
  }

  render() {
    if (this.state.error) {
      return (
        <ErrorReport
          title="화면을 그리는 중 오류가 발생했습니다"
          error={this.state.error}
          info={this.state.info}
        />
      );
    }
    return this.props.children;
  }
}

/** 오버레이 DOM이 이미 붙어 있는지 표시하는 body 속성. */
const OVERLAY_FLAG = "data-oc-error-overlay";

/**
 * 렌더 바깥(모듈 로드·비동기 콜백)에서 터진 예외를 잡아 화면에 표시한다.
 * ErrorBoundary는 렌더 중 예외만 잡으므로 이 핸들러가 나머지를 보완한다.
 *
 * 오버레이는 React 루트가 아니라 body 직속 엘리먼트에 그린다. 예전에는 루트를
 * `innerHTML = ""`로 비웠는데, 그러면 React의 파이버 트리는 그대로인 채 DOM만
 * 사라져서 다음 커밋 때 "removeChild: 지우려는 노드가 이 노드의 자식이 아니다"로
 * 앱이 죽었다. 원래 오류는 그 뒤에 묻혀 보이지도 않았다.
 */
export function installGlobalErrorOverlay() {
  const show = (title, error) => {
    if (document.body.hasAttribute(OVERLAY_FLAG)) return;
    document.body.setAttribute(OVERLAY_FLAG, "1");
    const stack = String(error?.stack || error || "알 수 없는 오류");
    const pre = document.createElement("pre");
    pre.textContent = `${title}\n\n${stack}`;
    pre.style.cssText =
      "position:fixed;inset:0;z-index:99999;overflow:auto;padding:24px;" +
      "background:#1a1a1a;color:#ff6b6b;font:13px/1.6 ui-monospace,Menlo,monospace;" +
      "white-space:pre-wrap;word-break:break-all;margin:0";
    document.body.appendChild(pre);
  };

  window.addEventListener("error", (event) => {
    show("스크립트 오류", event.error || event.message);
  });
  window.addEventListener("unhandledrejection", (event) => {
    show("처리되지 않은 Promise 거부", event.reason);
  });
}
