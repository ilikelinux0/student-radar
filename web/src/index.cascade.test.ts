import { describe, expect, it } from "vitest";

import css from "./index.css?raw";

describe("dashboard css", () => {
  it("puts the button font reset in the base layer so unread weight can apply", () => {
    const base = css.match(/@layer base\s*\{[\s\S]*?\n\}/);
    expect(base?.[0]).toMatch(/button[\s\S]*font:\s*inherit/);
  });
});
