import { fold } from "../logic";
import {
  DONE,
  FIT_FLOOR,
  PERSONAL_CATEGORIES,
  PERSONAL_MARKS,
  TRACKING,
  type PersonalMark,
  type PersonalOffer,
  type PersonalView,
} from "./types";

const DAY_MS = 86_400_000;

/** Whole days from today (UTC) to the deadline. Negative once it has passed. */
export function daysLeft(deadline: string | null, now: Date): number | null {
  if (!deadline) return null;
  const target = Date.parse(`${deadline.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(target)) return null;
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return Math.round((target - today) / DAY_MS);
}

/** Red at 3 days or fewer, amber at 14 or fewer. */
export function urgency(days: number | null): "red" | "amber" | null {
  if (days === null || days < 0) return null;
  if (days <= 3) return "red";
  if (days <= 14) return "amber";
  return null;
}

export function dayLabel(days: number | null): string | null {
  if (days === null) return null;
  if (days < 0) return "closed";
  if (days === 0) return "today";
  return `D-${days}`;
}

export function isClosed(offer: PersonalOffer, now: Date): boolean {
  if (offer.closed) return true;
  const days = daysLeft(offer.deadline, now);
  return days !== null && days < 0;
}

function finished(mark: PersonalMark | undefined): boolean {
  return mark === "ignored" || (mark !== undefined && DONE.includes(mark));
}

/** Something to act on: not ruled out, not closed, not already finished. */
function inPlay(offer: PersonalOffer, mark: PersonalMark | undefined, now: Date) {
  return (
    offer.eligibility !== "ineligible" && !isClosed(offer, now) && !finished(mark)
  );
}

function relevant(offer: PersonalOffer): boolean {
  return (offer.fit ?? -1) >= FIT_FLOOR;
}

export function matchesPersonalView(
  offer: PersonalOffer,
  mark: PersonalMark | undefined,
  view: PersonalView,
  now: Date,
): boolean {
  const days = daysLeft(offer.deadline, now);
  switch (view) {
    case "soon":
      return inPlay(offer, mark, now) && relevant(offer) && days !== null && days >= 0;
    case "open":
      return (
        inPlay(offer, mark, now) &&
        relevant(offer) &&
        offer.application_status !== "not_yet_open" &&
        (days === null || offer.deadline_kind === "rolling")
      );
    case "watching":
      return (
        offer.application_status === "not_yet_open" &&
        offer.eligibility !== "ineligible" &&
        !finished(mark)
      );
    case "tracking":
      return mark !== undefined && TRACKING.includes(mark);
    case "done":
      return mark !== undefined && DONE.includes(mark);
    case "ineligible":
      return offer.eligibility === "ineligible";
    case "closed":
      return isClosed(offer, now);
    case "all":
      return true;
    case "health":
      return false;
  }
}

export function comparePersonal(
  view: PersonalView,
  now: Date,
): (left: PersonalOffer, right: PersonalOffer) => number {
  return (left, right) => {
    if (view === "soon" || view === "tracking") {
      const leftDays = daysLeft(left.deadline, now) ?? Number.MAX_SAFE_INTEGER;
      const rightDays = daysLeft(right.deadline, now) ?? Number.MAX_SAFE_INTEGER;
      if (leftDays !== rightDays) return leftDays - rightDays;
    }
    const fit = (right.fit ?? -1) - (left.fit ?? -1);
    if (fit !== 0) return fit;
    return Date.parse(right.last_seen_at) - Date.parse(left.last_seen_at);
  };
}

export function matchesPersonalQuery(offer: PersonalOffer, query: string): boolean {
  const needle = fold(query.trim());
  if (!needle) return true;
  const haystack = fold(
    [
      offer.provider,
      offer.program_name,
      offer.category,
      offer.location,
      offer.best_mention?.title,
      offer.best_mention?.summary,
      offer.best_mention?.excerpt,
    ]
      .filter(Boolean)
      .join("\n"),
  );
  return haystack.includes(needle);
}

export function visiblePersonal(
  offers: PersonalOffer[],
  marks: Record<string, PersonalMark>,
  view: PersonalView,
  category: string | null,
  query: string,
  now: Date,
): PersonalOffer[] {
  return offers
    .filter((offer) => matchesPersonalView(offer, marks[offer.offer_key], view, now))
    .filter((offer) => (category ? offer.category === category : true))
    .filter((offer) => matchesPersonalQuery(offer, query))
    .sort(comparePersonal(view, now));
}

export function personalCategories(offers: PersonalOffer[]): string[] {
  const present = new Set(offers.map((offer) => offer.category).filter(Boolean));
  return PERSONAL_CATEGORIES.filter((name) => present.has(name));
}

export function exportPersonalMarks(marks: Record<string, PersonalMark>): string {
  return JSON.stringify({ version: 1, personal: marks }, null, 2) + "\n";
}

export function importPersonalMarks(text: string): Record<string, PersonalMark> {
  const parsed: unknown = JSON.parse(text);
  const incoming =
    parsed && typeof parsed === "object"
      ? (parsed as { version?: unknown; personal?: unknown })
      : null;
  if (
    !incoming ||
    incoming.version !== 1 ||
    typeof incoming.personal !== "object" ||
    incoming.personal === null
  ) {
    throw new Error("Status file must be a version 1 personal file");
  }
  const marks: Record<string, PersonalMark> = {};
  for (const [key, value] of Object.entries(incoming.personal)) {
    if (PERSONAL_MARKS.includes(value as PersonalMark)) {
      marks[key] = value as PersonalMark;
    }
  }
  return marks;
}

export function categoryLabel(category: string | null): string {
  if (!category) return "unscored";
  return category.replace("_", " ");
}
