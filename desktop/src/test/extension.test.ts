// The Chrome extension's page scripts (extension/src/content), tested in jsdom. They are plain scripts that put their
// API on globalThis, as they run when the service worker injects them into a job page.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

type Capture = {
  capture(): Record<string, string>;
  readJsonLd(root?: Document): Record<string, string> | null;
  htmlToText(html: string): string;
  canonicalUrl(href: string): string;
};
type Field = { field_id: string; label: string; type: string; options: string[]; required: boolean; max_length: number | null };
type Form = {
  scan(): Field[];
  fill(
    answers: { field_id: string; value: string | null; source: string; note?: string }[],
    options?: { keepFilled?: boolean },
  ): { filled: number; needs: number; failed: number; kept: number };
  watchSteps(): void;
};
type Watch = { looksConfirmed(doc?: Document, href?: string): boolean };
type PageState = {
  jobPostingKey(url: string): string | null;
  scopeLinkedState(linked: Record<string, unknown> | null, url: string): { state: Record<string, unknown> | null; changed: boolean };
  snapshotMatchesPosting(snapshotPostingKey: string | null, url: string): boolean;
};

const sendMessage = vi.fn();
const g = globalThis as unknown as { __resumeflowCapture: Capture; __resumeflowForm: Form; __resumeflowWatch: Watch; chrome: unknown; CSS: unknown };
let pageState: PageState;

beforeAll(async () => {
  g.chrome = { runtime: { sendMessage } };
  g.CSS = { escape: (value: string) => value.replace(/["\\]/g, "\\$&") };
  // jsdom has no layout: report every element as rendered so the form reader sees it.
  Element.prototype.getClientRects = function () {
    return [{ width: 10, height: 10 }] as unknown as DOMRectList;
  };
  // @ts-expect-error a plain page script without types
  await import("../../../extension/src/content/capture.js");
  // @ts-expect-error a plain page script without types
  await import("../../../extension/src/content/form.js");
  // @ts-expect-error a plain page script without types
  await import("../../../extension/src/content/watch.js");
  // @ts-expect-error the extension helper is plain JavaScript
  pageState = await import("../../../extension/src/lib/page-state.js");
});

describe("keeping side-panel state with the correct posting", () => {
  it("recognizes the same LinkedIn or Indeed job across their SPA and canonical URLs", () => {
    expect(pageState.jobPostingKey("https://www.linkedin.com/jobs/search/?currentJobId=4012345678&keywords=data")).toBe("linkedin:4012345678");
    expect(pageState.jobPostingKey("https://www.linkedin.com/jobs/view/4012345678/")).toBe("linkedin:4012345678");
    expect(pageState.jobPostingKey("https://www.indeed.com/jobs?q=python&vjk=abc123")).toBe("indeed:abc123");
  });

  it("drops stale linked ids when a different job is selected in the same tab", () => {
    const linked = { jobId: "job-a", applicationId: "app-a", postingKey: "linkedin:111", autoAnswer: true };
    expect(pageState.scopeLinkedState(linked, "https://www.linkedin.com/jobs/search/?currentJobId=222")).toEqual({
      state: { postingKey: "linkedin:222" },
      changed: true,
    });
    expect(pageState.snapshotMatchesPosting("linkedin:111", "https://www.linkedin.com/jobs/view/222/")).toBe(false);
  });

  it("keeps linked state when the same posting changes from a search URL to its canonical URL", () => {
    const linked = { jobId: "job-a", applicationId: "app-a", postingKey: "linkedin:111" };
    expect(pageState.scopeLinkedState(linked, "https://www.linkedin.com/jobs/view/111/")).toEqual({ state: linked, changed: false });
    expect(pageState.snapshotMatchesPosting("linkedin:111", "https://www.linkedin.com/jobs/view/111/")).toBe(true);
  });

  it("preserves the application link while the tab moves to an ATS form", () => {
    const linked = { jobId: "job-a", applicationId: "app-a", postingKey: "linkedin:111", autoAnswer: true };
    expect(pageState.scopeLinkedState(linked, "https://careers.example.com/application/step/2")).toEqual({ state: linked, changed: false });
    expect(pageState.snapshotMatchesPosting("linkedin:111", "https://careers.example.com/application/step/2")).toBe(true);
    expect(pageState.jobPostingKey("https://boards.greenhouse.io/acme/jobs/42/apply")).toBeNull();
  });
});

beforeEach(() => {
  document.head.innerHTML = "";
  document.body.innerHTML = "";
  sendMessage.mockReset();
});

describe("capturing a job posting", () => {
  it("reads schema.org JobPosting data, including @graph and HTML descriptions", () => {
    const posting = {
      "@context": "https://schema.org",
      "@graph": [
        { "@type": "Organization", name: "Somebody else" },
        {
          "@type": "JobPosting",
          title: "Senior Data Engineer",
          hiringOrganization: { "@type": "Organization", name: "Acme Data Co" },
          jobLocation: { "@type": "Place", address: { addressLocality: "Austin", addressRegion: "TX" } },
          employmentType: ["FULL_TIME"],
          description: "<p>Build pipelines.</p><ul><li>Python</li><li>Spark &amp; Airflow</li></ul>",
        },
      ],
    };
    document.head.innerHTML = `<script type="application/ld+json">${JSON.stringify(posting)}</script>`;
    const job = g.__resumeflowCapture.readJsonLd();
    expect(job).toMatchObject({ title: "Senior Data Engineer", company: "Acme Data Co", location: "Austin, TX", employment_type: "FULL_TIME" });
    expect(job?.description).toBe("Build pipelines.\n\n- Python\n- Spark & Airflow");

    const captured = g.__resumeflowCapture.capture();
    expect(captured).toMatchObject({ title: "Senior Data Engineer", company: "Acme Data Co", read_by: "structured data" });
  });

  it("uses a long text selection as the description", () => {
    document.body.innerHTML = `<h1>Platform Engineer</h1><p id="jd">${"We need someone to run Kubernetes clusters and build CI/CD. ".repeat(6)}</p>`;
    const range = document.createRange();
    range.selectNodeContents(document.getElementById("jd")!);
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    const captured = g.__resumeflowCapture.capture();
    expect(captured.read_by).toBe("selection");
    expect(captured.title).toBe("Platform Engineer");
    expect(captured.description).toContain("Kubernetes clusters");
    window.getSelection()!.removeAllRanges();
  });

  it("keeps the posting's own address", () => {
    const { canonicalUrl } = g.__resumeflowCapture;
    expect(canonicalUrl("https://www.linkedin.com/jobs/search/?currentJobId=4012345678&keywords=data")).toBe("https://www.linkedin.com/jobs/view/4012345678/");
    expect(canonicalUrl("https://www.indeed.com/jobs?q=python&vjk=abc123")).toBe("https://www.indeed.com/viewjob?jk=abc123");
    expect(canonicalUrl("https://boards.greenhouse.io/acme/jobs/42?gh_src=x&utm_source=li#app")).toBe("https://boards.greenhouse.io/acme/jobs/42?gh_src=x");
  });
});

const FORM = `
  <form>
    <label for="fn">First Name *</label><input id="fn" name="first_name" required />
    <label>Email <input id="em" type="email" /></label>
    <fieldset><legend>Are you legally authorized to work in the United States?</legend>
      <label><input type="radio" name="auth" value="1" /> Yes</label>
      <label><input type="radio" name="auth" value="0" /> No</label>
    </fieldset>
    <label for="g">Gender</label>
    <select id="g"><option value="">Select…</option><option value="m">Male</option><option value="d">I decline to self-identify</option></select>
    <label for="why">Why do you want to work here?</label><textarea id="why" maxlength="500"></textarea>
    <label><input id="certify" type="checkbox" /> I certify that the information above is true and complete</label>
    <label for="cv">Resume/CV</label><input id="cv" type="file" />
    <input type="hidden" name="token" value="x" />
    <button type="submit">Submit application</button>
  </form>`;

describe("reading and filling an application form", () => {
  it("lists the fields with their labels, types and options", () => {
    document.body.innerHTML = FORM;
    const fields = g.__resumeflowForm.scan();
    const byLabel = Object.fromEntries(fields.map((f) => [f.label, f]));
    expect(fields.map((f) => f.type)).toEqual(["text", "email", "radio", "select", "textarea", "checkbox", "file"]);
    expect(byLabel["First Name"]).toMatchObject({ required: true });
    expect(byLabel["Email"]).toBeDefined();
    expect(byLabel["Are you legally authorized to work in the United States?"].options).toEqual(["Yes", "No"]);
    expect(byLabel["Gender"].options).toEqual(["Male", "I decline to self-identify"]);
    expect(byLabel["Why do you want to work here?"].max_length).toBe(500);
  });

  it("fills answers, leaves the user's fields marked, and never ticks a certification box", () => {
    document.body.innerHTML = FORM;
    const fields = g.__resumeflowForm.scan();
    const id = (type: string) => fields.find((f) => f.type === type)!.field_id;
    const onInput = vi.fn();
    document.getElementById("fn")!.addEventListener("input", onInput);

    const result = g.__resumeflowForm.fill([
      { field_id: id("text"), value: "Avery", source: "profile" },
      { field_id: id("radio"), value: "Yes", source: "answer_bank" },
      { field_id: id("select"), value: "I decline to self-identify", source: "answer_bank" },
      { field_id: id("textarea"), value: "I build reliable data pipelines.", source: "ai" },
      { field_id: id("checkbox"), value: "Yes", source: "profile" }, // even if asked, a certification is never ticked
      { field_id: id("email"), value: null, source: "needs_input", note: "Not in the profile" },
    ]);

    expect((document.getElementById("fn") as HTMLInputElement).value).toBe("Avery");
    expect(onInput).toHaveBeenCalled(); // frameworks see the change
    expect((document.querySelector("input[name='auth'][value='1']") as HTMLInputElement).checked).toBe(true);
    expect((document.getElementById("g") as HTMLSelectElement).value).toBe("d");
    expect((document.getElementById("why") as HTMLTextAreaElement).value).toBe("I build reliable data pipelines.");
    expect((document.getElementById("certify") as HTMLInputElement).checked).toBe(false);
    expect(result).toEqual({ filled: 4, needs: 1, failed: 1, kept: 0 });
    expect(document.getElementById("em")!.title).toContain("Not in the profile");
  });

  it("fills the next step of a multi-step form in its own fields, not the hidden earlier step", () => {
    document.body.innerHTML = `<form><label for="a">First Name</label><input id="a" /></form>`;
    const [first] = g.__resumeflowForm.scan();
    // Step 2: step 1 stays in the page, hidden; a new field appears.
    document.getElementById("a")!.style.display = "none";
    document.querySelector("form")!.insertAdjacentHTML("beforeend", `<label for="b">Years of experience</label><input id="b" />`);
    const step2 = g.__resumeflowForm.scan();
    expect(step2.map((f) => f.label)).toEqual(["Years of experience"]); // only what the user can see
    expect(step2[0].field_id).not.toBe(first.field_id);
    g.__resumeflowForm.fill([{ field_id: step2[0].field_id, value: "9", source: "resume" }]);
    expect((document.getElementById("b") as HTMLInputElement).value).toBe("9");
    expect((document.getElementById("a") as HTMLInputElement).value).toBe("");
  });

  it("keeps what is already in a field when a new step is answered automatically", () => {
    document.body.innerHTML = `<label for="a">First Name</label><input id="a" value="Ave" /><label for="b">City</label><input id="b" />`;
    const fields = g.__resumeflowForm.scan() as (Field & { filled: boolean })[];
    expect(fields.map((f) => f.filled)).toEqual([true, false]); // automatic answers only ask about the empty one
    const answers = fields.map((f) => ({ field_id: f.field_id, value: f.label === "First Name" ? "Avery" : "Austin", source: "profile" }));
    const result = g.__resumeflowForm.fill(answers, { keepFilled: true });
    expect((document.getElementById("a") as HTMLInputElement).value).toBe("Ave");
    expect((document.getElementById("b") as HTMLInputElement).value).toBe("Austin");
    expect(result).toMatchObject({ filled: 1, kept: 1 });
  });

  it("reports a new step once its fields appear, and nothing for fields already read", async () => {
    vi.useFakeTimers();
    try {
      document.body.innerHTML = `<label for="a">First Name</label><input id="a" />`;
      g.__resumeflowForm.scan();
      g.__resumeflowForm.watchSteps();
      document.getElementById("a")!.style.outline = "2px solid green"; // ResumeFlow's own marking
      await Promise.resolve();
      vi.advanceTimersByTime(1000);
      expect(sendMessage).not.toHaveBeenCalled();
      document.body.insertAdjacentHTML("beforeend", `<label for="q">Why do you want to join?</label><textarea id="q"></textarea>`);
      await Promise.resolve();
      vi.advanceTimersByTime(1000);
      expect(sendMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "form-step" }));
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("recognizing the confirmation page", () => {
  it("knows a confirmation from its text or address", () => {
    const { looksConfirmed } = g.__resumeflowWatch;
    document.body.innerHTML = "<h1>Thank you for applying!</h1><p>We will be in touch.</p>";
    expect(looksConfirmed(document, "https://boards.example.com/acme/jobs/42")).toBe(true);
    document.body.innerHTML = "<h1>Application</h1><p>Thanks — your application was received.</p>";
    expect(looksConfirmed(document, "https://jobs.example.com/apply/confirmation")).toBe(true);
    document.body.innerHTML = "<h1>Apply for Data Engineer</h1><button>Submit application</button>";
    expect(looksConfirmed(document, "https://boards.example.com/acme/jobs/42/apply")).toBe(false);
  });

  it("tells the service worker when the user clicks Submit", () => {
    document.body.innerHTML = FORM;
    document.querySelector("form")!.addEventListener("submit", (event) => event.preventDefault());
    (document.querySelector("button[type='submit']") as HTMLButtonElement).click();
    expect(sendMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "submit-clicked" }));
  });

  it("doesn't take the Next button of a multi-step form for Submit", () => {
    document.body.innerHTML = `<form><label for="x">City</label><input id="x" /><button type="submit">Next</button></form>`;
    document.querySelector("form")!.addEventListener("submit", (event) => event.preventDefault());
    (document.querySelector("button[type='submit']") as HTMLButtonElement).click();
    expect(sendMessage).not.toHaveBeenCalledWith(expect.objectContaining({ type: "submit-clicked" }));
  });
});
