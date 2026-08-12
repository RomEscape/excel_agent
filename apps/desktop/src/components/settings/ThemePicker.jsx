/**
 * ThemePicker — 화면 테마 3값 선택 (시스템 / 라이트 / 다크).
 *
 * 조합만 한다. 규칙은 lib/theme.js, 적용은 lib/themeManager.js, 상태는
 * store/themeStore.js가 소유한다.
 */
import React from "react";
import { Monitor, Moon, Sun } from "lucide-react";

import { cn } from "@/lib/utils";
import { THEME_LABELS, THEME_PREFERENCES } from "@/lib/theme";
import { setThemePreference } from "@/lib/themeManager";
import useThemeStore from "@/store/themeStore";

const ICONS = { system: Monitor, light: Sun, dark: Moon };

export default function ThemePicker() {
  const preference = useThemeStore((s) => s.preference);
  const resolved = useThemeStore((s) => s.resolved);

  return (
    <div className="flex flex-col gap-2">
      <div
        className="flex w-fit gap-1 rounded-lg border border-border bg-muted p-1"
        role="radiogroup"
        aria-label="화면 테마"
      >
        {THEME_PREFERENCES.map((pref) => {
          const Icon = ICONS[pref];
          const active = preference === pref;
          return (
            <button
              key={pref}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => setThemePreference(pref)}
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                active
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {THEME_LABELS[pref]}
            </button>
          );
        })}
      </div>
      {preference === "system" && (
        <p className="text-xs text-muted-foreground">
          현재 운영체제 설정에 따라 <b>{resolved === "dark" ? "다크" : "라이트"}</b>로
          보이고 있어요.
        </p>
      )}
    </div>
  );
}
