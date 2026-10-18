import type { Offer, SourceRow } from "./types";

export const externalLinkRel = "noopener noreferrer";

const FOLDS: Record<string, string> = {
  İ: "i",
  I: "i",
  ı: "i",
  Ş: "s",
  ş: "s",
  Ğ: "g",
  ğ: "g",
  Ü: "u",
  ü: "u",
  Ö: "o",
  ö: "o",
  Ç: "c",
  ç: "c",
};

export function fold(text: string): string {
  return text
    .replace(/[İIıŞşĞğÜüÖöÇç]/g, (char) => FOLDS[char] ?? char)
    .toLowerCase();
}

export function relativeTime(iso: string, now: Date): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const minutes = Math.floor(Math.max(0, now.getTime() - then) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 14) return `${days}d ago`;
  return new Date(then).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

export function offerHeading(offer: Offer): {
  title: string;
  detail: string | null;
} {
  if (offer.provider || offer.program_name) {
    return {
      title: offer.provider ?? "Unknown",
      detail: offer.program_name,
    };
  }
  return {
    title: offer.best_mention?.title ?? "Unknown",
    detail: null,
  };
}

export function linkHost(url: string | undefined): string | null {
  const safe = safeUrl(url);
  if (!safe) return null;
  return new URL(safe).host;
}

export function safeUrl(url: string | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.href;
    }
  } catch {
    return null;
  }
  return null;
}

// Storage can throw (blocked site data, some private windows). Marks and theme
// are conveniences, so the reader keeps working without them.
export function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStored(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Nothing to do: the value lasts until the tab closes.
  }
}

export function healthOrder(sources: SourceRow[]): SourceRow[] {
  return [...sources].sort((left, right) => {
    const leftBad = left.status === "error" || left.failures > 0 ? 0 : 1;
    const rightBad = right.status === "error" || right.failures > 0 ? 0 : 1;
    if (leftBad !== rightBad) return leftBad - rightBad;
    if (right.failures !== left.failures) return right.failures - left.failures;
    return left.name.localeCompare(right.name);
  });
}
