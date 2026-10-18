import { Row, Section } from "../components/Sidebar";
import { categoryLabel, matchesPersonalView, personalCategories } from "./logic";
import type {
  PersonalMark,
  PersonalOffer,
  PersonalSourceRow,
  PersonalView,
} from "./types";

const VIEWS: [PersonalView, string][] = [
  ["soon", "Closing soon"],
  ["open", "Open, no deadline"],
  ["watching", "Watching"],
  ["tracking", "Tracking"],
  ["done", "Done"],
  ["ineligible", "Ineligible"],
  ["closed", "Closed"],
  ["all", "All"],
];

export function PersonalSidebar({
  offers,
  sources,
  marks,
  view,
  category,
  now,
  dark,
  onView,
  onCategory,
  onExport,
  onImport,
  onTheme,
}: {
  offers: PersonalOffer[];
  sources: PersonalSourceRow[];
  marks: Record<string, PersonalMark>;
  view: PersonalView;
  category: string | null;
  now: Date;
  dark: boolean;
  onView: (view: PersonalView) => void;
  onCategory: (category: string | null) => void;
  onExport: () => void;
  onImport: (file: File) => void;
  onTheme: () => void;
}) {
  const watched = sources.filter((source) => source.usually_opens);
  return (
    <aside className="flex h-full flex-col bg-[var(--panel)] text-[13px]">
      <div className="flex items-baseline justify-between px-4 pt-4 pb-3">
        <div>
          <p className="text-[11px] tracking-[0.18em] text-[var(--accent)] uppercase">
            Student
          </p>
          <h1 className="text-[18px] font-medium tracking-tight">Radar</h1>
        </div>
        <button
          type="button"
          className="text-[12px] text-[var(--muted)]"
          onClick={onTheme}
        >
          {dark ? "Light" : "Dark"}
        </button>
      </div>
      <nav className="flex-1 overflow-y-auto px-2 pb-3">
        <Section label="Views">
          {VIEWS.map(([id, label]) => (
            <Row
              key={id}
              label={label}
              count={
                offers.filter((offer) =>
                  matchesPersonalView(offer, marks[offer.offer_key], id, now),
                ).length
              }
              active={view === id && !category}
              onClick={() => onView(id)}
            />
          ))}
        </Section>
        <Section label="Categories">
          {personalCategories(offers).map((name) => (
            <Row
              key={name}
              label={categoryLabel(name)}
              count={offers.filter((offer) => offer.category === name).length}
              active={category === name}
              onClick={() => onCategory(category === name ? null : name)}
            />
          ))}
        </Section>
        {watched.length > 0 ? (
          <Section label="Usually opens">
            {watched.map((source) => (
              <p
                key={source.id}
                className="flex justify-between gap-2 px-2 py-1 text-[12px] text-[var(--muted)]"
              >
                <span className="truncate text-[var(--ink)]">{source.name}</span>
                <span className="shrink-0">{source.usually_opens}</span>
              </p>
            ))}
          </Section>
        ) : null}
        <Section label="Library">
          <Row
            label="Source health"
            count={sources.filter((source) => source.status === "error").length}
            active={view === "health"}
            onClick={() => onView("health")}
          />
        </Section>
      </nav>
      <div className="flex gap-3 border-t border-[var(--line)] px-4 py-3 text-[12px] text-[var(--muted)]">
        <button type="button" onClick={onExport}>
          Export
        </button>
        <label className="cursor-pointer">
          Import
          <input
            type="file"
            accept="application/json"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onImport(file);
              event.target.value = "";
            }}
          />
        </label>
      </div>
    </aside>
  );
}
