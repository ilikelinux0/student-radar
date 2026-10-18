import { describe, expect, it } from "vitest";

import {
  dayLabel,
  daysLeft,
  exportPersonalMarks,
  importPersonalMarks,
  matchesPersonalView,
  urgency,
  visiblePersonal,
} from "./logic";
import { personalOffer } from "./fixtures";
import type { PersonalOffer } from "./types";

const NOW = new Date("2026-10-12T09:00:00Z");

describe("deadlines", () => {
  it("counts whole UTC days and marks red, amber, and closed", () => {
    expect(daysLeft("2026-10-12", NOW)).toBe(0);
    expect(daysLeft("2026-10-15", NOW)).toBe(3);
    expect(daysLeft("2026-10-11", NOW)).toBe(-1);
    expect(daysLeft(null, NOW)).toBeNull();
    expect(urgency(3)).toBe("red");
    expect(urgency(14)).toBe("amber");
    expect(urgency(15)).toBeNull();
    expect(urgency(-1)).toBeNull();
    expect(dayLabel(0)).toBe("today");
    expect(dayLabel(8)).toBe("D-8");
    expect(dayLabel(-2)).toBe("closed");
  });
});

describe("personal views", () => {
  it("closing soon keeps relevant, open, eligible-or-unclear deadlines", () => {
    const keep = personalOffer();
    const unclear = personalOffer({ offer_key: "u", eligibility: "unclear" });
    const ineligible = personalOffer({ offer_key: "i", eligibility: "ineligible" });
    const past = personalOffer({ offer_key: "p", deadline: "2026-10-01" });
    const lowFit = personalOffer({ offer_key: "l", fit: 3 });
    const closed = personalOffer({ offer_key: "c", closed: true });
    const noDeadline = personalOffer({ offer_key: "n", deadline: null });
    const view = (offer: PersonalOffer) =>
      matchesPersonalView(offer, undefined, "soon", NOW);

    expect(view(keep)).toBe(true);
    expect(view(unclear)).toBe(true);
    expect(view(ineligible)).toBe(false);
    expect(view(past)).toBe(false);
    expect(view(lowFit)).toBe(false);
    expect(view(closed)).toBe(false);
    expect(view(noDeadline)).toBe(false);
    expect(matchesPersonalView(keep, "ignored", "soon", NOW)).toBe(false);
    expect(matchesPersonalView(keep, "rejected", "soon", NOW)).toBe(false);
    expect(matchesPersonalView(keep, "applied", "soon", NOW)).toBe(true);
  });

  it("sorts closing soon by days left", () => {
    const later = personalOffer({ offer_key: "later", deadline: "2026-11-11", fit: 10 });
    const sooner = personalOffer({ offer_key: "sooner", deadline: "2026-10-14", fit: 6 });

    const listed = visiblePersonal([later, sooner], {}, "soon", null, "", NOW);

    expect(listed.map((offer) => offer.offer_key)).toEqual(["sooner", "later"]);
  });

  it("routes not-yet-open, tracked, finished, and ineligible offers", () => {
    const waiting = personalOffer({
      offer_key: "w",
      deadline: null,
      application_status: "not_yet_open",
      fit: 2,
    });
    const ineligible = personalOffer({ offer_key: "i", eligibility: "ineligible" });

    expect(matchesPersonalView(waiting, undefined, "watching", NOW)).toBe(true);
    expect(matchesPersonalView(waiting, undefined, "open", NOW)).toBe(false);
    expect(matchesPersonalView(personalOffer(), "interview", "tracking", NOW)).toBe(true);
    expect(matchesPersonalView(personalOffer(), "offer", "done", NOW)).toBe(true);
    expect(matchesPersonalView(ineligible, undefined, "ineligible", NOW)).toBe(true);
    expect(matchesPersonalView(ineligible, undefined, "all", NOW)).toBe(true);
  });

  it("lists rolling and undated offers under open", () => {
    const rolling = personalOffer({ deadline_kind: "rolling" });
    const undated = personalOffer({ deadline: null, deadline_kind: "unknown" });

    expect(matchesPersonalView(rolling, undefined, "open", NOW)).toBe(true);
    expect(matchesPersonalView(undated, undefined, "open", NOW)).toBe(true);
  });
});

describe("personal marks", () => {
  it("round-trips known marks and drops anything else", () => {
    const text = exportPersonalMarks({ a: "interview", b: "rejected" });
    const tampered = text.replace('"rejected"', '"hired-by-script"');

    expect(importPersonalMarks(text)).toEqual({ a: "interview", b: "rejected" });
    expect(importPersonalMarks(tampered)).toEqual({ a: "interview" });
    expect(() => importPersonalMarks('{"version": 1, "statuses": {}}')).toThrow();
  });
});
