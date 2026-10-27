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
  fill(answers: { field_id: string; value: string | null; source: string; note?: string }[]): { filled: number; needs: number; failed: number };
};
type Watch = { looksConfirmed(doc?: Document, href?: string): boolean };

const sendMessage = vi.fn();
const g = globalThis as unknown as { __resumeflowCapture: Capture; __resumeflowForm: Form; __resumeflowWatch: Watch; chrome: unknown; CSS: unknown };

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
    expect(result).toEqual({ filled: 4, needs: 1, failed: 1 });
    expect(document.getElementById("em")!.title).toContain("Not in the profile");
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
});
