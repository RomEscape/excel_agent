import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge Tailwind class names, resolving conflicts with tailwind-merge.
 * Mirrors the convention used by shadcn/ui.
 *
 * @param {...import('clsx').ClassValue} inputs
 * @returns {string}
 */
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

/**
 * ISO 날짜 문자열 또는 Date 객체를 한국어 상대 시간 문자열로 변환한다.
 *
 * - 1분 미만: "방금 전"
 * - 1~59분:   "N분 전"
 * - 1~23시간: "N시간 전"
 * - 1일:      "어제"
 * - 2일:      "그저께"
 * - 3~6일:    "N일 전"
 * - 7일 이상, 올해: "M월 D일"
 * - 7일 이상, 작년 이전: "YYYY년 M월 D일"
 *
 * @param {string | Date | null | undefined} date
 * @returns {string}
 */
export function relativeTime(date) {
  if (!date) return "-";
  const d = date instanceof Date ? date : new Date(date);
  if (isNaN(d.getTime())) return "-";

  const now = new Date();
  const diffMs = now - d;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHour / 24);

  if (diffSec < 60) return "방금 전";
  if (diffMin < 60) return `${diffMin}분 전`;
  if (diffHour < 24) return `${diffHour}시간 전`;
  if (diffDay === 1) return "어제";
  if (diffDay === 2) return "그저께";
  if (diffDay < 7) return `${diffDay}일 전`;

  const thisYear = now.getFullYear();
  const targetYear = d.getFullYear();
  const month = d.getMonth() + 1;
  const day = d.getDate();

  if (targetYear === thisYear) {
    return `${month}월 ${day}일`;
  }
  return `${targetYear}년 ${month}월 ${day}일`;
}
