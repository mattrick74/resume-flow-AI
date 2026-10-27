import { describe, expect, it } from "vitest";

import { linkAddresses } from "@/components/review/ResumeDocument";

describe("contact links in the resume preview", () => {
  it("shows the address itself and leaves out bare labels", () => {
    expect(linkAddresses(["LinkedIn", "https://www.linkedin.com/in/jane-doe/", "GitHub: github.com/jane", "jane@example.com"])).toEqual([
      "linkedin.com/in/jane-doe",
      "github.com/jane",
    ]);
    expect(linkAddresses(["linkedin.com/in/jane-doe", "https://linkedin.com/in/jane-doe"])).toEqual(["linkedin.com/in/jane-doe"]);
  });
});
