import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import ErrorBoundary, { installGlobalErrorOverlay } from "@/components/ui/error-boundary";
import "./index.css";

const rootElement = document.getElementById("root");

installGlobalErrorOverlay(rootElement);

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
