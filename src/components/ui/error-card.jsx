/**
 * ErrorCard — shared error display component for user-facing error messages.
 *
 * Usage:
 *   import { ErrorCard } from "@/components/ui/error-card";
 *   <ErrorCard message="오류 메시지" />
 */

import React from "react";

/**
 * @param {{ message: string }} props
 */
export function ErrorCard({ message }) {
  return (
    <div
      role="alert"
      className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
    >
      {message}
    </div>
  );
}

export default ErrorCard;
