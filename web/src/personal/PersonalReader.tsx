import { externalLinkRel, linkHost, offerHeading, relativeTime, safeUrl } from "../logic";
import { categoryLabel, dayLabel, daysLeft, isClosed, urgency } from "./logic";
import { PERSONAL_MARKS, type PersonalMark, type PersonalOffer } from "./types";

const MARK_LABELS: Record<PersonalMark, string> = {
  interested: "Interested",
  applied: "Applied",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  seen: "Seen",
  ignored: "Ignore",
};

export function PersonalReader({
  offer,
  mark,
  now,
  onBack,
  onMark,
}: {
  offer: PersonalOffer | null;
  mark: PersonalMark | undefined;
  now: Date;
  onBack: () => void;
  onMark: (mark: PersonalMark | undefined) => void;
}) {
  if (!offer) {
    return (
      <section className="hidden h-full items-center justify-center px-8 text-[13px] text-[var(--muted)] md:flex">
        Select an opportunity.
      </section>
    );
  }
  const link = safeUrl(offer.best_mention?.url);
  const host = linkHost(offer.best_mention?.url);
  const heading = offerHeading(offer);
  const days = daysLeft(offer.deadline, now);
  const tone = urgency(days);
  const closed = isClosed(offer, now);
  return (
    <article className="h-full overflow-y-auto px-5 py-4 md:px-10 md:py-8">
      <button
        type="button"
        className="mb-4 text-[13px] text-[var(--muted)] md:hidden"
        onClick={onBack}
      >
        Back
      </button>
      <p className="text-[11px] tracking-[0.16em] text-[var(--accent)] uppercase">
        {categoryLabel(offer.category)}
        {" · "}
        {mark ?? "new"}
        {offer.fit === null ? "" : ` · fit ${offer.fit}`}
      </p>
      <h2 className="reader-title mt-2 text-[34px] leading-none">{heading.title}</h2>
      {heading.detail ? (
        <p className="mt-1 text-[16px] text-[var(--muted)]">{heading.detail}</p>
      ) : null}

      <Eligibility offer={offer} />

      <dl className="mt-5 grid max-w-xl grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[13px]">
        <dt className="text-[var(--muted)]">Deadline</dt>
        <dd
          className={
            tone === "red"
              ? "text-[var(--danger)]"
              : tone === "amber"
                ? "text-[var(--accent)]"
                : undefined
          }
        >
          {offer.deadline
            ? `${offer.deadline_approx ? "about " : ""}${offer.deadline}`
            : offer.deadline_kind === "rolling"
              ? "Rolling"
              : "Not stated"}
          {offer.deadline && dayLabel(days) ? ` · ${dayLabel(days)}` : ""}
        </dd>
        <dt className="text-[var(--muted)]">Applications</dt>
        <dd>
          {closed
            ? "Closed"
            : offer.application_status === "not_yet_open"
              ? `Not open yet${offer.usually_opens ? ` · usually ${offer.usually_opens}` : ""}`
              : offer.application_status === "open"
                ? "Open"
                : "Unknown"}
        </dd>
        {offer.starts_at ? (
          <>
            <dt className="text-[var(--muted)]">Dates</dt>
            <dd>
              {offer.starts_at}
              {offer.ends_at ? ` – ${offer.ends_at}` : ""}
            </dd>
          </>
        ) : null}
        {offer.location || offer.remote ? (
          <>
            <dt className="text-[var(--muted)]">Where</dt>
            <dd>
              {offer.location ?? ""}
              {offer.remote ? (offer.location ? " · remote" : "Remote") : ""}
            </dd>
          </>
        ) : null}
        {offer.amount ? (
          <>
            <dt className="text-[var(--muted)]">Value</dt>
            <dd>{offer.amount}</dd>
          </>
        ) : null}
      </dl>

      <div className="mt-5 flex flex-wrap gap-2 text-[13px]">
        {PERSONAL_MARKS.map((name) => (
          <button
            key={name}
            type="button"
            aria-pressed={mark === name}
            onClick={() => onMark(mark === name ? undefined : name)}
            className={`border border-[var(--line)] px-3 py-1 ${
              mark === name ? "bg-[var(--selected)]" : "hover:bg-[var(--chip)]"
            }`}
          >
            {MARK_LABELS[name]}
          </button>
        ))}
        {link ? (
          <a
            className="border border-[var(--line)] px-3 py-1"
            href={link}
            target="_blank"
            rel={externalLinkRel}
          >
            {host ? `Open · ${host}` : "Open"}
          </a>
        ) : null}
      </div>

      {offer.best_mention?.summary ? (
        <p className="reader-title mt-8 max-w-xl text-[22px] leading-snug">
          {offer.best_mention.summary}
        </p>
      ) : (
        <p className="mt-8 text-[14px] text-[var(--muted)]">Not scored yet.</p>
      )}
      {offer.best_mention?.reasoning ? (
        <p className="mt-3 max-w-xl text-[14px] leading-relaxed text-[var(--muted)]">
          {offer.best_mention.reasoning}
        </p>
      ) : null}
      {offer.best_mention?.excerpt ? (
        <blockquote className="mt-6 max-w-xl border-l border-[var(--accent)] pl-4 text-[13px] leading-relaxed whitespace-pre-line text-[var(--muted)]">
          {offer.best_mention.excerpt}
        </blockquote>
      ) : null}
      <h3 className="mt-8 text-[11px] tracking-[0.16em] text-[var(--muted)] uppercase">
        Seen in
      </h3>
      <ul className="mt-2 max-w-xl divide-y divide-[var(--line)]">
        {offer.mentions.map((mention, index) => {
          const href = safeUrl(mention.url);
          return (
            <li key={`${index}-${mention.url}-${mention.date}`} className="py-2 text-[13px]">
              {href ? (
                <a
                  href={href}
                  target="_blank"
                  rel={externalLinkRel}
                  className="underline decoration-[var(--line)]"
                >
                  {mention.title}
                </a>
              ) : (
                <span>{mention.title}</span>
              )}
              <span className="mt-0.5 block text-[11px] text-[var(--muted)]">
                {mention.source}
                {href ? ` · ${linkHost(mention.url)}` : ""}
                {" · "}
                {relativeTime(mention.date, now)}
              </span>
            </li>
          );
        })}
      </ul>
    </article>
  );
}

function Eligibility({ offer }: { offer: PersonalOffer }) {
  if (offer.eligibility === null) return null;
  const text =
    offer.eligibility === "eligible"
      ? "You meet every stated requirement."
      : offer.eligibility === "ineligible"
        ? `Not eligible: ${offer.eligibility_reason ?? "a requirement is not met"}.`
        : `Check eligibility: ${offer.eligibility_reason ?? "a requirement could not be checked"}.`;
  return (
    <p
      className={`mt-4 max-w-xl border-l-2 pl-3 text-[13px] ${
        offer.eligibility === "ineligible"
          ? "border-[var(--danger)] text-[var(--danger)]"
          : offer.eligibility === "unclear"
            ? "border-[var(--accent)]"
            : "border-[var(--line)] text-[var(--muted)]"
      }`}
    >
      {text}
    </p>
  );
}
