import { describe, expect, it } from "vitest";
import { futurePageConfig, navigationItems } from "./navigation";

describe("navigation contract", () => {
  it("exposes exactly ten unique first-level destinations", () => {
    expect(navigationItems).toHaveLength(10);
    expect(new Set(navigationItems.map((item) => item.path)).size).toBe(10);
    expect(new Set(navigationItems.map((item) => item.label)).size).toBe(10);
  });

  it("gives each deferred module an honest next step", () => {
    for (const page of Object.values(futurePageConfig)) {
      expect(page.title).not.toBe("");
      expect(page.action).not.toBe("");
      expect(page.hint).toContain("Stage 01");
    }
  });
});
