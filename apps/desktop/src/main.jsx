import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./index.css";
import { initTheme } from "@/lib/themeManager";
import { initFontScale } from "@/lib/fontScaleManager";

// 첫 페인트 전에 <html>에 .dark를 세운다. App의 useEffect까지 미루면
// 다크 사용자에게 한 프레임 동안 흰 화면이 번쩍인다.
// App도 같은 함수를 부르지만 themeManager가 중복 구독을 막는다.
initTheme();

// 같은 이유로 루트 font-size도 첫 페인트 전에 세운다 — 나중에 세우면
// `큰 사이즈` 사용자에게 글자가 한 번 작았다가 커지는 게 보인다.
initFontScale();

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
