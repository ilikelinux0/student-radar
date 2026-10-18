import { healthOrder, relativeTime } from "../logic";
import type { SourceRow } from "../types";

export function Health({ sources, now }: { sources: SourceRow[]; now: Date }) {
  const rows = healthOrder(sources);
  return (
    <section className="h-full overflow-y-auto px-4 py-4 md:px-8">
      <h2 className="reader-title text-[28px]">Source health</h2>
      <p className="mt-1 text-[13px] text-[var(--muted)]">
        Failing sources are listed first.
      </p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[760px] border-collapse text-left text-[13px]">
          <thead className="text-[11px] tracking-[0.12em] text-[var(--muted)] uppercase">
            <tr className="border-b border-[var(--line)]">
              <th className="py-2 font-medium">Source</th>
              <th className="py-2 font-medium">Category</th>
              <th className="py-2 font-medium">Type</th>
              <th className="py-2 font-medium">Status</th>
              <th className="py-2 font-medium">Failures</th>
              <th className="py-2 font-medium">Checked</th>
              <th className="py-2 font-medium">Found</th>
              <th className="py-2 font-medium">Note</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((source) => {
              const failing = source.status === "error" || source.failures > 0;
              return (
                <tr key={source.id} className="border-b border-[var(--line)]">
                  <td className={`py-2 ${failing ? "text-[var(--danger)]" : ""}`}>
                    {source.name}
                  </td>
                  <td className="py-2 text-[var(--muted)]">{source.category}</td>
                  <td className="py-2 text-[var(--muted)]">{source.type}</td>
                  <td className="py-2">{source.status ?? "—"}</td>
                  <td className="py-2 tabular-nums">{source.failures}</td>
                  <td className="py-2 text-[var(--muted)]">
                    {source.last_checked
                      ? relativeTime(source.last_checked, now)
                      : "—"}
                  </td>
                  <td className="py-2 tabular-nums">
                    {source.items_found_last_run ?? "—"}
                  </td>
                  <td className="max-w-[280px] py-2 text-[var(--muted)]">
                    {source.last_error ?? "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
