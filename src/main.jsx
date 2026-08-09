import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import ErrorBoundary, { installGlobalErrorOverlay } from "@/components/ui/error-boundary";
import "./index.css";

const rootElement = document.getElementById("root");

installGlobalErrorOverlay();

// HMR이나 중복 평가로 이 모듈이 다시 실행될 때 createRoot를 또 부르면 같은 DOM을
// root 두 개가 관리하게 된다. 그러면 한쪽이 이미 지운 노드를 다른 쪽이 또 지우려 하면서
// "removeChild: 지우려는 노드가 이 노드의 자식이 아니다"로 화면이 통째로 죽는다.
// 컨테이너당 root는 하나만 만들고, 재실행 때는 그 root에 다시 render한다.
if (!rootElement.__ocReactRoot) {
  rootElement.__ocReactRoot = ReactDOM.createRoot(rootElement);
}

rootElement.__ocReactRoot.render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
