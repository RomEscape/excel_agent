import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";

// CI의 `npm run lint --if-present`는 지금까지 스크립트가 없어 조용히 스킵됐다.
// 전체 코드의 38.9%인 프론트엔드가 검사를 한 번도 안 받았다는 뜻이다.
export default [
  {
    ignores: [
      "dist/**",
      "node_modules/**",
      "src-tauri/target/**",
      "python-sidecar/**",
      "logs/**",
    ],
  },
  {
    files: ["**/*.{js,jsx,mjs}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...globals.browser, ...globals.node },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      // 대문자로 시작하는 미사용 변수는 컴포넌트·상수 import라 경고에서 뺀다.
      "no-unused-vars": [
        "error",
        { varsIgnorePattern: "^[A-Z_]", argsIgnorePattern: "^_" },
      ],
      // 빈 catch는 "실패해도 그냥 간다"는 뜻으로 일부러 쓴 자리가 많다.
      "no-empty": ["error", { allowEmptyCatch: true }],

      // --- 아래는 "틀렸다"가 아니라 "미뤘다" ---
      // CI를 초록으로 되돌리는 커밋에 컴포넌트 대량 수정을 섞으면 리뷰가 안 된다.
      // 경고로 두어 보이게는 하되 빌드를 막지는 않는다. 별도 커밋에서 올린다.
      "react-hooks/exhaustive-deps": "warn",
      "react-hooks/set-state-in-effect": "warn", // 25곳
      "preserve-caught-error": "warn", // 3곳. ruff의 B904와 같은 성격
      "no-useless-assignment": "warn", // 7곳
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
    },
  },
];
