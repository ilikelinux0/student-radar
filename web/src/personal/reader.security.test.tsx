import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { personalOffer } from "./fixtures";
import { PersonalReader } from "./PersonalReader";

const NOW = new Date("2026-10-12T09:00:00Z");

describe("personal reader text", () => {
  it("escapes model text, links safely, and shows the eligibility reason", () => {
    const offer = personalOffer({
      provider: "<b>Example</b>",
      eligibility: "ineligible",
      eligibility_reason: "<script>alert(1)</script>",
      deadline: "2026-10-14",
      deadline_approx: true,
      best_mention: {
        title: "<script>alert(1)</script>",
        source: "feed",
        url: "https://example.com/offer",
        date: "2026-10-11T12:00:00.000000+00:00",
        score: 8,
        excerpt: "<img src=x onerror=alert(1)>",
        summary: "<img src=x onerror=alert(1)>",
        reasoning: "<iframe src=javascript:alert(1)>",
      },
      mentions: [
        {
          title: "<script>alert(1)</script>",
          source: "feed",
          url: "javascript:alert(1)",
          date: "2026-10-11T12:00:00.000000+00:00",
          score: 8,
        },
      ],
    });

    const html = renderToStaticMarkup(
      <PersonalReader
        offer={offer}
        mark="applied"
        now={NOW}
        onBack={() => undefined}
        onMark={() => undefined}
      />,
    );

    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<iframe");
    expect(html).not.toContain("<b>");
    expect(html).not.toContain('href="javascript:');
    expect(html.match(/rel="noopener noreferrer"/g)).toHaveLength(1);
    expect(html).toContain("Not eligible: &lt;script&gt;");
    expect(html).toContain("about 2026-10-14 · D-2");
    expect(html).toContain('aria-pressed="true"');
  });
});
