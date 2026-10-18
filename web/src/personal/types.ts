import type { Offer, SnapshotMeta, SourceRow } from "../types";

export type PersonalMark =
  | "interested"
  | "applied"
  | "interview"
  | "offer"
  | "rejected"
  | "withdrawn"
  | "seen"
  | "ignored";

export type PersonalView =
  | "soon"
  | "open"
  | "watching"
  | "tracking"
  | "done"
  | "ineligible"
  | "closed"
  | "all"
  | "health";

export type Eligibility = "eligible" | "ineligible" | "unclear";

export interface PersonalOffer extends Offer {
  fit: number | null;
  eligibility: Eligibility | null;
  eligibility_reason: string | null;
  application_status: "open" | "not_yet_open" | "closed" | "unknown";
  deadline_kind: "apply_by" | "event_date" | "rolling" | "unknown";
  deadline_approx: boolean;
  starts_at: string | null;
  ends_at: string | null;
  location: string | null;
  remote: boolean | null;
  closed: boolean;
  usually_opens: string | null;
}

export interface PersonalSourceRow extends SourceRow {
  usually_opens: string | null;
  url: string | null;
}

export interface PersonalOffersFile {
  meta: SnapshotMeta;
  offers: PersonalOffer[];
}

export interface PersonalSourcesFile {
  meta: SnapshotMeta;
  sources: PersonalSourceRow[];
}

export const PERSONAL_MARKS: PersonalMark[] = [
  "interested",
  "applied",
  "interview",
  "offer",
  "rejected",
  "withdrawn",
  "seen",
  "ignored",
];

export const TRACKING: PersonalMark[] = ["interested", "applied", "interview"];
export const DONE: PersonalMark[] = ["offer", "rejected", "withdrawn"];

// Below this fit an offer only shows in All, Watching, and the status views.
export const FIT_FLOOR = 5;

export const PERSONAL_CATEGORIES = [
  "internship",
  "job",
  "graduate_program",
  "grant",
  "training",
  "certification",
  "event",
  "competition",
  "other",
] as const;
