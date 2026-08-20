import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./index.css";
import { initTheme } from "@/lib/themeManager";

// 첫 페인트 전에 <html>에 .dark를 세운다. App의 useEffect까지 미루면
// 다크 사용자에게 한 프레임 동안 흰 화면이 번쩍인다.
// App도 같은 함수를 부르지만 themeManager가 중복 구독을 막는다.
initTheme();

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
