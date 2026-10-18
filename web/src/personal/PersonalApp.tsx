import { useEffect, useMemo, useRef, useState } from "react";

import { Health } from "../components/Health";
import { readStored, safeUrl, writeStored } from "../logic";
import { exportPersonalMarks, importPersonalMarks, visiblePersonal } from "./logic";
import { PersonalList } from "./PersonalList";
import { PersonalReader } from "./PersonalReader";
import { PersonalSidebar } from "./PersonalSidebar";
import type {
  PersonalMark,
  PersonalOffer,
  PersonalOffersFile,
  PersonalSourceRow,
  PersonalSourcesFile,
  PersonalView,
} from "./types";

const STATUS_KEY = "radar-personal-status";

function loadMarks(): Record<string, PersonalMark> {
  try {
    const text = readStored(STATUS_KEY);
    return text ? importPersonalMarks(text) : {};
  } catch {
    return {};
  }
}

export default function PersonalApp() {
  const [offers, setOffers] = useState<PersonalOffer[] | null>(null);
  const [sources, setSources] = useState<PersonalSourceRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<PersonalView>("soon");
  const [category, setCategory] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [marks, setMarks] = useState<Record<string, PersonalMark>>(loadMarks);
  const [navOpen, setNavOpen] = useState(false);
  const [detail, setDetail] = useState(false);
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  const searchRef = useRef<HTMLInputElement>(null);
  const now = useMemo(() => new Date(), []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetch("/data/offers.json").then((response) => {
        if (!response.ok) throw new Error("offers.json");
        return response.json() as Promise<PersonalOffersFile>;
      }),
      fetch("/data/sources.json").then((response) => {
        if (!response.ok) throw new Error("sources.json");
        return response.json() as Promise<PersonalSourcesFile>;
      }),
    ])
      .then(([offerFile, sourceFile]) => {
        if (cancelled) return;
        setOffers(offerFile.offers);
        setSources(sourceFile.sources);
      })
      .catch(() => {
        if (!cancelled) {
          setError("No results yet. Run the sweep once (see the README), then reload.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    writeStored(STATUS_KEY, exportPersonalMarks(marks));
  }, [marks]);

  const listed = useMemo(
    () => (offers ? visiblePersonal(offers, marks, view, category, query, now) : []),
    [offers, marks, view, category, query, now],
  );
  const current =
    offers?.find((offer) => offer.offer_key === selected) ?? listed[0] ?? null;

  function choose(next: string) {
    setSelected(next);
    setDetail(true);
  }

  function move(delta: number) {
    if (listed.length === 0) return;
    const index = listed.findIndex((offer) => offer.offer_key === current?.offer_key);
    const next = listed[Math.min(listed.length - 1, Math.max(0, index + delta))];
    if (next) choose(next.offer_key);
  }

  function setMark(mark: PersonalMark | undefined) {
    if (!current) return;
    setMarks((prev) => {
      const next = { ...prev };
      if (mark === undefined) delete next[current.offer_key];
      else next[current.offer_key] = mark;
      return next;
    });
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing =
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement;
      if (event.key === "/" && !typing) {
        event.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (typing) return;
      const mark = current ? marks[current.offer_key] : undefined;
      if (event.key === "j") move(1);
      if (event.key === "k") move(-1);
      if (event.key === "o") {
        const link = safeUrl(current?.best_mention?.url);
        if (link) window.open(link, "_blank", "noopener,noreferrer");
      }
      if (event.key === "m") setMark(mark === undefined ? "seen" : undefined);
      if (event.key === "i") setMark("interested");
      if (event.key === "a") setMark("applied");
      if (event.key === "v") setMark("interview");
      if (event.key === "x") setMark("ignored");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className="flex h-dvh flex-col bg-[var(--bg)] text-[var(--ink)] md:grid md:grid-cols-[232px_minmax(300px,420px)_1fr]">
      <div className={`${navOpen ? "fixed inset-0 z-20" : "hidden"} md:static md:block`}>
        <PersonalSidebar
          offers={offers ?? []}
          sources={sources ?? []}
          marks={marks}
          view={view}
          category={category}
          now={now}
          dark={dark}
          onView={(next) => {
            setView(next);
            setCategory(null);
            setDetail(false);
            setNavOpen(false);
          }}
          onCategory={(next) => {
            setCategory(next);
            setView("all");
            setDetail(false);
            setNavOpen(false);
          }}
          onExport={() => {
            const blob = new Blob([exportPersonalMarks(marks)], {
              type: "application/json",
            });
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = "radar-personal-status.json";
            link.click();
            URL.revokeObjectURL(url);
          }}
          onImport={(file) => {
            void file.text().then((text) => {
              try {
                setMarks(importPersonalMarks(text));
              } catch {
                setError("That status file is not a personal status file.");
              }
            });
          }}
          onTheme={() => {
            const next = !dark;
            setDark(next);
            document.documentElement.classList.toggle("dark", next);
            writeStored("radar-theme", next ? "dark" : "light");
          }}
        />
      </div>
      <div className="flex min-h-0 flex-1 flex-col md:contents">
        <header className="flex items-center justify-between border-b border-[var(--line)] px-3 py-2 md:hidden">
          <button type="button" onClick={() => setNavOpen(true)}>
            Menu
          </button>
          <span className="text-[13px] tracking-[0.14em] uppercase">Student Radar</span>
          <button type="button" onClick={() => searchRef.current?.focus()}>
            Search
          </button>
        </header>
        {error ? (
          <p className="px-4 py-8 text-[13px] text-[var(--danger)]">{error}</p>
        ) : offers === null || sources === null ? (
          <p className="px-4 py-8 text-[13px] text-[var(--muted)]">
            Loading the snapshot…
          </p>
        ) : view === "health" ? (
          <div className="min-h-0 flex-1 md:col-span-2">
            <Health sources={sources} now={now} />
          </div>
        ) : (
          <>
            <div className={`${detail ? "hidden" : "min-h-0 flex-1"} md:block md:min-h-0`}>
              <PersonalList
                offers={listed}
                marks={marks}
                selected={current?.offer_key ?? null}
                query={query}
                now={now}
                searchRef={searchRef}
                onQuery={setQuery}
                onSelect={choose}
              />
            </div>
            <div className={`${detail ? "min-h-0 flex-1" : "hidden"} md:block md:min-h-0`}>
              <PersonalReader
                offer={current}
                mark={current ? marks[current.offer_key] : undefined}
                now={now}
                onBack={() => setDetail(false)}
                onMark={setMark}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
