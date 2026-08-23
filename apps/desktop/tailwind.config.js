/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: [
    "./index.html",
    "./src/**/*.{js,jsx,ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        // 브랜드 원값 — 글자를 얹지 않는 자리 전용 (대비 사유는 index.css 주석 참조)
        // 텍스트 회색 계단 — foreground/muted-foreground 사이를 메운다.
        // `text-` 유틸과 이름이 겹치지 않도록 ink 네임스페이스를 쓴다.
        ink: {
          body: "hsl(var(--ink-body))",
          subtle: "hsl(var(--ink-subtle))",
          faint: "hsl(var(--ink-faint))",
          disabled: "hsl(var(--ink-disabled))",
        },
        // 작업 기록 상태 배지
        status: {
          done: "hsl(var(--status-done))",
          "done-bg": "hsl(var(--status-done-bg))",
          blocked: "hsl(var(--status-blocked))",
          "blocked-bg": "hsl(var(--status-blocked-bg))",
        },
        brand: {
          DEFAULT: "hsl(var(--brand))",
          step: "hsl(var(--brand-step))",
          file: "hsl(var(--brand-file))",
          glow: "hsl(var(--brand-glow))",
        },
        "chat-bubble": {
          DEFAULT: "hsl(var(--chat-bubble))",
          foreground: "hsl(var(--chat-bubble-foreground))",
        },
        sidebar: {
          DEFAULT: "hsl(var(--sidebar-background))",
          foreground: "hsl(var(--sidebar-foreground))",
          primary: "hsl(var(--sidebar-primary))",
          "primary-foreground": "hsl(var(--sidebar-primary-foreground))",
          accent: "hsl(var(--sidebar-accent))",
          "accent-foreground": "hsl(var(--sidebar-accent-foreground))",
          border: "hsl(var(--sidebar-border))",
          ring: "hsl(var(--sidebar-ring))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [],
};
