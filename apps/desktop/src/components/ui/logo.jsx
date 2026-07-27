import { cn } from "@/lib/utils";
import logoLight from "@/assets/brand-logo-light.svg";
import logoDark from "@/assets/brand-logo-dark.svg";
import wordmark from "@/assets/brand-wordmark.svg";

/**
 * 브랜드 로고 UI primitive (표시 전용, 재사용 컴포넌트).
 *
 * - <BrandMark/>     : 정사각 앱 아이콘(라운드 타일). 테마별 SVG 스왑(light/dark).
 * - <BrandWordmark/> : 로고 마크 + "김대리" 텍스트 워드마크(투명 배경 단일 에셋 —
 *                       녹색 글리프라 light/dark 모두에서 읽힘).
 *
 * 브랜드 에셋 경로는 이 한 곳에서만 관리한다. 다른 컴포넌트(Sidebar 등)는
 * 이 primitive를 조합만 하고 에셋을 직접 import 하지 않는다.
 */
export function BrandMark({ className }) {
  return (
    <>
      <img
        src={logoLight}
        alt="김대리"
        className={cn("block dark:hidden", className)}
      />
      <img
        src={logoDark}
        alt="김대리"
        className={cn("hidden dark:block", className)}
      />
    </>
  );
}

export function BrandWordmark({ className }) {
  return <img src={wordmark} alt="김대리" className={className} />;
}
