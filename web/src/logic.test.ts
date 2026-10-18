import { afterEach, describe, expect, it, vi } from "vitest";

import {
  externalLinkRel,
  fold,
  healthOrder,
  linkHost,
  offerHeading,
  readStored,
  relativeTime,
  safeUrl,
  writeStored,
} from "./logic";
import type { Offer, SourceRow } from "./types";

const NOW = new Date("2026-10-11T20:00:00Z");

function offer(partial: Partial<Offer> & Pick<Offer, "offer_key">): Offer {
  return {
    provider: "Example",
    program_name: "Program",
    best_score: 5,
    amount: null,
    deadline: null,
    first_seen_at: "2026-10-01T00:00:00.000000+00:00",
    last_seen_at: "2026-10-11T12:00:00.000000+00:00",
    mention_count: 1,
    status: "new",
    category: "internship",
    best_mention: {
      title: "A note",
      source: "feed",
      url: "https://example.com/a",
      date: "2026-10-11T12:00:00.000000+00:00",
      score: partial.best_score ?? 5,
      excerpt: "summer internship",
      summary: "A summary",
      reasoning: "A reason",
    },
    mentions: [
      {
        title: "A note",
        source: "feed",
        url: "https://example.com/a",
        date: "2026-10-11T12:00:00.000000+00:00",
        score: partial.best_score ?? 5,
      },
    ],
    ...partial,
  };
}

describe("shared helpers", () => {
  it("folds Turkish letters and prints relative times", () => {
    expect(fold("İstanbul Şirket")).toBe("istanbul sirket");
    expect(relativeTime("2026-10-11T17:00:00.000000+00:00", NOW)).toBe("3h ago");
    expect(relativeTime("2026-10-11T19:40:00.000000+00:00", NOW)).toBe("20m ago");
  });

  it("names an offer from its mention when the provider is missing", () => {
    const row = offer({
      offer_key: "legacy",
      provider: null,
      program_name: null,
    });
    expect(offerHeading(row)).toEqual({
      title: "A note",
      detail: null,
    });
    expect(offerHeading(offer({ offer_key: "named" }))).toEqual({
      title: "Example",
      detail: "Program",
    });
  });

  it("opens only http links and lists failing sources first", () => {
    expect(linkHost("https://jobs.example.com/intern/")).toBe("jobs.example.com");
    expect(linkHost("javascript:alert(1)")).toBeNull();
    expect(safeUrl("https://example.com/a")).toBe("https://example.com/a");
    expect(safeUrl("javascript:alert(1)")).toBeNull();
    expect(externalLinkRel).toBe("noopener noreferrer");
    const sources: SourceRow[] = [
      {
        id: "ok",
        name: "Ok",
        category: "ai",
        type: "rss",
        last_checked: null,
        status: "ok",
        failures: 0,
        items_found_last_run: 1,
        last_error: null,
      },
      {
        id: "bad",
        name: "Bad",
        category: "ai",
        type: "rss",
        last_checked: null,
        status: "error",
        failures: 2,
        items_found_last_run: 0,
        last_error: "HTTP 403",
      },
    ];
    expect(healthOrder(sources).map((row) => row.id)).toEqual(["bad", "ok"]);
  });
});

describe("browser storage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads and writes nothing instead of throwing when storage is blocked", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new DOMException("blocked", "SecurityError");
      },
      setItem: () => {
        throw new DOMException("blocked", "SecurityError");
      },
    });

    expect(readStored("radar-theme")).toBeNull();
    expect(() => writeStored("radar-theme", "dark")).not.toThrow();
  });

  it("round-trips a value when storage works", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
    });

    writeStored("radar-theme", "light");
    expect(readStored("radar-theme")).toBe("light");
  });
});
