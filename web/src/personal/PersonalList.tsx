import { useEffect, useRef, type RefObject } from "react";

import { offerHeading } from "../logic";
import { categoryLabel, dayLabel, daysLeft, urgency } from "./logic";
import type { PersonalMark, PersonalOffer } from "./types";

export function PersonalList({
  offers,
  marks,
  selected,
  query,
  now,
  onQuery,
  onSelect,
  searchRef,
}: {
  offers: PersonalOffer[];
  marks: Record<string, PersonalMark>;
  selected: string | null;
  query: string;
  now: Date;
  onQuery: (query: string) => void;
  onSelect: (offerKey: string) => void;
  searchRef: RefObject<HTMLInputElement | null>;
}) {
  return (
    <section className="flex h-full min-h-0 flex-col border-[var(--line)] md:border-r">
      <div className="flex items-center gap-2 border-b border-[var(--line)] px-3 py-2">
        <input
          ref={searchRef}
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          placeholder="Search"
          aria-label="Search"
          className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-[var(--muted)]"
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {offers.length === 0 ? (
          <p className="px-4 py-8 text-[13px] text-[var(--muted)]">
            Nothing in this view.
          </p>
        ) : (
          offers.map((offer) => (
            <PersonalRow
              key={offer.offer_key}
              offer={offer}
              mark={marks[offer.offer_key]}
              selected={selected === offer.offer_key}
              now={now}
              onSelect={onSelect}
            />
          ))
        )}
      </div>
    </section>
  );
}

function PersonalRow({
  offer,
  mark,
  selected,
  now,
  onSelect,
}: {
  offer: PersonalOffer;
  mark: PersonalMark | undefined;
  selected: boolean;
  now: Date;
  onSelect: (offerKey: string) => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);
  const heading = offerHeading(offer);
  const days = daysLeft(offer.deadline, now);
  const tone = urgency(days);
  const label = dayLabel(days);
  return (
    <button
      ref={ref}
      type="button"
      onClick={() => onSelect(offer.offer_key)}
      className={`grid w-full grid-cols-[28px_1fr_auto] items-start gap-3 border-b border-[var(--line)] px-3 py-2.5 text-left ${
        selected ? "bg-[var(--selected)]" : ""
      } ${mark === undefined ? "font-semibold" : "font-normal text-[var(--muted)]"}`}
    >
      <FitBadge fit={offer.fit} />
      <span className="min-w-0">
        <span className="block truncate text-[13px] text-[var(--ink)]">
          {heading.title}{" "}
          {heading.detail ? (
            <span className="font-normal text-[var(--muted)]">{heading.detail}</span>
          ) : null}
        </span>
        <span className="mt-0.5 block truncate text-[11px] font-normal text-[var(--muted)]">
          {categoryLabel(offer.category)}
          {offer.location ? ` · ${offer.location}` : ""}
          {offer.eligibility && offer.eligibility !== "eligible"
            ? ` · ${offer.eligibility}`
            : ""}
          {mark && mark !== "seen" ? ` · ${mark}` : ""}
        </span>
      </span>
      <span className="text-right">
        {label ? (
          <span
            className={`block text-[12px] font-semibold tabular-nums ${
              tone === "red"
                ? "text-[var(--danger)]"
                : tone === "amber"
                  ? "text-[var(--accent)]"
                  : "text-[var(--muted)]"
            }`}
          >
            {label}
          </span>
        ) : offer.usually_opens ? (
          <span className="block text-[11px] font-normal text-[var(--muted)]">
            {offer.usually_opens}
          </span>
        ) : null}
        {offer.deadline ? (
          <span className="mt-0.5 block text-[11px] font-normal text-[var(--muted)] tabular-nums">
            {offer.deadline_approx ? "~" : ""}
            {offer.deadline}
          </span>
        ) : null}
      </span>
    </button>
  );
}

export function FitBadge({ fit }: { fit: number | null }) {
  const tone =
    fit === null
      ? "bg-[var(--chip)] text-[var(--muted)]"
      : fit >= 9
        ? "bg-[#e2b15a] text-[#241806]"
        : fit >= 7
          ? "bg-[#315c40] text-[#e7f6ea]"
          : fit >= 4
            ? "bg-[#7a5424] text-[#f8ecd4]"
            : "bg-[#3c4146] text-[#d5d7da]";
  return (
    <span
      className={`mt-0.5 inline-flex h-7 w-7 items-center justify-center text-[12px] font-semibold tabular-nums ${tone}`}
    >
      {fit ?? "–"}
    </span>
  );
}
