/**
 * Spinner — shared loading indicator for AI calls.
 *
 * Usage:
 *   import { Spinner } from "@/components/ui/spinner";
 *   <Spinner label="처리 중..." />
 */

import React from "react";

/**
 * @param {{ label?: string }} props
 */
export function Spinner({ label = "처리 중..." }) {
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <svg
        className="h-4 w-4 animate-spin"
        viewBox="0 0 24 24"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        <circle
          className="opacity-25"
          cx="12"
          cy="12"
          r="10"
          stroke="currentColor"
          strokeWidth="4"
        />
        <path
          className="opacity-75"
          fill="currentColor"
          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
        />
      </svg>
      <span>{label}</span>
    </div>
  );
}

export default Spinner;
