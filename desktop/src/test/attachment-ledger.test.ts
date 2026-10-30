import { beforeAll, describe, expect, it } from "vitest";

type FileValue = { filename: string; mime: string; base64: string };
type LedgerEntry = { fingerprint: string; status: "attempted" | "attached" | "failed"; recordedAt: number };
type Ledger = Record<string, LedgerEntry>;
type LedgerApi = {
  attachmentFingerprint(file: FileValue): string;
  recordAttachment(
    ledger: Ledger,
    options: {
      applicationId: number;
      answerHost: string;
      kind: string;
      fingerprint: string;
      status: LedgerEntry["status"];
      recordedAt?: number;
    },
  ): Ledger;
  shouldAttachFile(
    ledger: Ledger,
    options: {
      applicationId: number;
      answerHost: string;
      kind: string;
      fingerprint: string | null;
      automatic: boolean;
    },
  ): boolean;
  forgetAttachment(ledger: Ledger, applicationId: number, answerHost: string, kind: string): Ledger;
};

let api: LedgerApi;

beforeAll(async () => {
  // @ts-expect-error the extension helper is plain JavaScript
  api = await import("../../../extension/src/lib/attachment-ledger.js");
});

describe("extension attachment ledger", () => {
  const scope = { applicationId: 42, answerHost: "jobs.example.com", kind: "resume" };
  const resume = { filename: "resume.pdf", mime: "application/pdf", base64: "JVBERi0xLjQ=" };

  it("blocks another automatic upload after an attempt survives a page reload", () => {
    const fingerprint = api.attachmentFingerprint(resume);
    expect(api.shouldAttachFile({}, { ...scope, fingerprint, automatic: true })).toBe(true);

    const attempted = api.recordAttachment({}, { ...scope, fingerprint, status: "attempted", recordedAt: 100 });
    // chrome.storage.session serializes the tab state; reproduce that boundary instead of relying on object identity.
    const afterReload = JSON.parse(JSON.stringify(attempted)) as Ledger;

    expect(api.shouldAttachFile(afterReload, { ...scope, fingerprint, automatic: true })).toBe(false);
    expect(api.shouldAttachFile(afterReload, { ...scope, fingerprint, automatic: false })).toBe(true);
  });

  it("does not repeat a confirmed file, but permits an explicit different-file replacement", () => {
    const fingerprint = api.attachmentFingerprint(resume);
    const attached = api.recordAttachment({}, { ...scope, fingerprint, status: "attached", recordedAt: 100 });
    const updatedFingerprint = api.attachmentFingerprint({ ...resume, base64: "JVBERi0xLjU=" });

    expect(api.shouldAttachFile(attached, { ...scope, fingerprint, automatic: true })).toBe(false);
    expect(api.shouldAttachFile(attached, { ...scope, fingerprint, automatic: false })).toBe(false);
    expect(api.shouldAttachFile(attached, { ...scope, fingerprint: updatedFingerprint, automatic: false })).toBe(true);
  });

  it("keeps applications, sites, and resume/cover-letter uploads independent", () => {
    const fingerprint = api.attachmentFingerprint(resume);
    const ledger = api.recordAttachment({}, { ...scope, fingerprint, status: "attached", recordedAt: 100 });

    expect(api.shouldAttachFile(ledger, { ...scope, applicationId: 43, fingerprint, automatic: true })).toBe(true);
    expect(api.shouldAttachFile(ledger, { ...scope, answerHost: "other.example.com", fingerprint, automatic: true })).toBe(true);
    expect(api.shouldAttachFile(ledger, { ...scope, kind: "cover_letter", fingerprint, automatic: true })).toBe(true);
  });

  it("allows a manual retry after a failed attachment while suppressing automatic loops", () => {
    const fingerprint = api.attachmentFingerprint(resume);
    const failed = api.recordAttachment({}, { ...scope, fingerprint, status: "failed", recordedAt: 100 });

    expect(api.shouldAttachFile(failed, { ...scope, fingerprint, automatic: true })).toBe(false);
    expect(api.shouldAttachFile(failed, { ...scope, fingerprint, automatic: false })).toBe(true);
  });

  it("normalizes host identity and can forget a definitely missing field attempt", () => {
    const fingerprint = api.attachmentFingerprint(resume);
    const attempted = api.recordAttachment(
      {},
      { ...scope, answerHost: " Jobs.Example.COM ", fingerprint, status: "attempted", recordedAt: 100 },
    );

    expect(api.shouldAttachFile(attempted, { ...scope, fingerprint, automatic: true })).toBe(false);
    const forgotten = api.forgetAttachment(attempted, scope.applicationId, scope.answerHost, scope.kind);
    expect(api.shouldAttachFile(forgotten, { ...scope, fingerprint, automatic: true })).toBe(true);
  });
});
