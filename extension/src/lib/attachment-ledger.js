// Durable file-attachment decisions for application forms. The ledger itself is stored in chrome.storage.session as
// part of tab state, so it survives content-script replacement, ATS rerenders, full page loads and service-worker sleep.

const normalized = (value) => String(value || "").trim().toLowerCase();

export function attachmentKey(applicationId, answerHost, kind) {
  return `${String(applicationId || "")}\0${normalized(answerHost)}\0${normalized(kind)}`;
}

/** A compact identity for a generated file. The file bytes/base64 are never persisted in extension storage. */
export function attachmentFingerprint(file) {
  const value = `${file?.filename || ""}\0${file?.mime || ""}\0${file?.base64 || ""}`;
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `rf-${(hash >>> 0).toString(36)}-${value.length}`;
}

export function attachmentEntry(ledger, applicationId, answerHost, kind) {
  return ledger?.[attachmentKey(applicationId, answerHost, kind)] || null;
}

/** Automatic passes never replace an attempted file. A manual pass can retry a failed/uncertain attempt or a new file. */
export function shouldAttachFile(ledger, { applicationId, answerHost, kind, fingerprint, automatic }) {
  const existing = attachmentEntry(ledger, applicationId, answerHost, kind);
  if (!existing) return true;
  if (automatic) return false;
  return existing.status !== "attached" || existing.fingerprint !== fingerprint;
}

export function recordAttachment(ledger, { applicationId, answerHost, kind, fingerprint, status, recordedAt = Date.now() }) {
  return {
    ...(ledger || {}),
    [attachmentKey(applicationId, answerHost, kind)]: { fingerprint, status, recordedAt },
  };
}

export function forgetAttachment(ledger, applicationId, answerHost, kind) {
  const next = { ...(ledger || {}) };
  delete next[attachmentKey(applicationId, answerHost, kind)];
  return next;
}
