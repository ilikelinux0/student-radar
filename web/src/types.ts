export interface Mention {
  title: string;
  source: string;
  url: string;
  date: string;
  score: number | null;
}

export interface BestMention extends Mention {
  excerpt: string;
  summary: string | null;
  reasoning: string | null;
}

export interface Offer {
  offer_key: string;
  provider: string | null;
  program_name: string | null;
  best_score: number | null;
  amount: string | null;
  deadline: string | null;
  first_seen_at: string;
  last_seen_at: string;
  mention_count: number;
  status: string;
  category: string | null;
  best_mention: BestMention | null;
  mentions: Mention[];
}

export interface SourceRow {
  id: string;
  name: string;
  category: string | null;
  type: string;
  last_checked: string | null;
  status: "ok" | "error" | null;
  failures: number;
  items_found_last_run: number | null;
  last_error: string | null;
}

export interface SnapshotMeta {
  generated_at: string;
  counts: { offers: number; mentions: number; sources: number };
  duration_seconds: number;
}
