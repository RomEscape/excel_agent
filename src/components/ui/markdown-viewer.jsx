/**
 * MarkdownViewer — Shared component that renders markdown content.
 *
 * Used by ExcelModule (report view) and DocumentModule (draft preview)
 * to avoid duplicating ReactMarkdown configuration across modules.
 *
 * Props:
 *   content   {string}  Markdown string to render
 *   className {string}  Optional extra CSS classes for the wrapper div
 */
import React from "react";
import ReactMarkdown from "react-markdown";

/**
 * @param {{ content: string, className?: string }} props
 */
export default function MarkdownViewer({ content, className = "" }) {
  return (
    <div
      className={[
        "prose prose-sm max-w-none rounded-lg border bg-muted/20 p-4 dark:prose-invert",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <ReactMarkdown>{content}</ReactMarkdown>
    </div>
  );
}
