import type { ReactNode } from "react";

export function Section({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="mt-3">
      <p className="px-2 pb-1 text-[10px] tracking-[0.16em] text-[var(--muted)] uppercase">
        {label}
      </p>
      {children}
    </div>
  );
}

export function Row({
  label,
  count,
  active,
  warn,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  warn?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center justify-between rounded px-2 py-1 text-left ${
        active ? "bg-[var(--selected)]" : "hover:bg-[var(--chip)]"
      }`}
    >
      <span className={warn ? "text-[var(--danger)]" : undefined}>{label}</span>
      <span className="text-[11px] text-[var(--muted)] tabular-nums">{count}</span>
    </button>
  );
}
