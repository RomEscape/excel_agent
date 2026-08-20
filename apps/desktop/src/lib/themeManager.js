/**
 * themeManager — 테마 도메인의 액션·부수효과 소유자.
 *
 * 실제로 색이 바뀌는 지점은 딱 하나다: `<html>`의 `.dark` 클래스.
 * tailwind.config.js가 `darkMode: ["class"]`라서 이 클래스 하나로 index.css의
 * `.dark {}` 블록 전체가 스왑된다.
 *
 * UI는 setThemePreference / toggleTheme만 부르고, 상태는 themeStore를 구독한다
 * (CLAUDE.md 안티패턴: "컴포넌트 안에 도메인 로직").
 */
import useThemeStore from "@/store/themeStore";
import { nextPreference } from "@/lib/theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";

/** 중복 구독 방지 — StrictMode의 이중 마운트에서도 리스너가 한 번만 붙는다. */
let unsubscribe = null;

/**
 * `<html class="dark">` 토글 + `color-scheme` 지정.
 *
 * color-scheme까지 같이 세우는 이유: 스크롤바·기본 폼 컨트롤처럼 OS가 그리는
 * 위젯은 CSS 변수를 안 보기 때문에, 이걸 빼면 다크 화면에 흰 스크롤바가 남는다.
 */
function paint(resolved) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved;
}

/**
 * 앱 시작 시 1회. OS 선호를 읽어 store에 넣고, 이후 변경도 따라간다.
 *
 * @returns {() => void} 정리 함수
 */
export function initTheme() {
  if (typeof window === "undefined") return () => {};
  if (unsubscribe) return unsubscribe;

  const mql = window.matchMedia(DARK_QUERY);
  const { setSystemDark } = useThemeStore.getState();

  setSystemDark(mql.matches);
  paint(useThemeStore.getState().resolved);

  const onSystemChange = (e) => setSystemDark(e.matches);
  mql.addEventListener("change", onSystemChange);

  // store가 바뀔 때마다(사용자 토글 포함) 다시 칠한다.
  const stopStore = useThemeStore.subscribe((state, prev) => {
    if (state.resolved !== prev?.resolved) paint(state.resolved);
  });

  unsubscribe = () => {
    mql.removeEventListener("change", onSystemChange);
    stopStore();
    unsubscribe = null;
  };
  return unsubscribe;
}

/** 설정 화면에서 3값 중 하나를 고를 때. */
export function setThemePreference(preference) {
  useThemeStore.getState().setPreference(preference);
}

/** 단축키·명령 팔레트용 — 지금 보이는 것의 반대로 넘긴다. */
export function toggleTheme() {
  const { preference, systemDark, setPreference } = useThemeStore.getState();
  setPreference(nextPreference(preference, systemDark));
}
