/**
 * EmptyState — Shared empty / placeholder state component.
 *
 * Displays a centered icon, title, description, and an optional action button.
 * Used when a list is empty or no file has been uploaded yet.
 *
 * Props:
 *   icon        {React.ElementType}  Lucide icon component
 *   title       {string}             Short heading (Korean)
 *   description {string}             Longer explanation (Korean)
 *   action      {{ label: string, onClick: () => void }} Optional CTA
 *   className   {string}             Optional extra wrapper classes
 */
import React from "react";
import { Button } from "@/components/ui/button";

/**
 * @param {{
 *   icon: React.ElementType,
 *   title: string,
 *   description?: string,
 *   action?: { label: string, onClick: () => void },
 *   className?: string
 * }} props
 */
export default function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className = "",
}) {
  return (
    <div
      className={[
        "flex flex-col items-center justify-center gap-3 py-12 text-center",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {Icon && (
        <Icon className="h-10 w-10 text-muted-foreground/40" aria-hidden="true" />
      )}
      <div className="space-y-1">
        <p className="text-sm font-medium text-foreground">{title}</p>
        {description && (
          <p className="text-xs text-muted-foreground">{description}</p>
        )}
      </div>
      {action && (
        <Button size="sm" variant="outline" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </div>
  );
}
