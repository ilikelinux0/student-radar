// A complete personal offer for tests. Each test overrides what it checks.
import type { PersonalOffer } from "./types";

export function personalOffer(overrides: Partial<PersonalOffer> = {}): PersonalOffer {
  return {
    offer_key: "example:program",
    provider: "Example",
    program_name: "Program",
    best_score: 8,
    amount: null,
    deadline: "2026-10-20",
    first_seen_at: "2026-10-01T00:00:00.000000+00:00",
    last_seen_at: "2026-10-11T00:00:00.000000+00:00",
    mention_count: 1,
    status: "new",
    category: "internship",
    best_mention: null,
    mentions: [],
    fit: 8,
    eligibility: "eligible",
    eligibility_reason: null,
    application_status: "open",
    deadline_kind: "apply_by",
    deadline_approx: false,
    starts_at: null,
    ends_at: null,
    location: "Istanbul",
    remote: false,
    closed: false,
    usually_opens: null,
    ...overrides,
  };
}
