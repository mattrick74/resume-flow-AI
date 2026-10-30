// ResumeFlow service worker: the only caller of the local backend, the bridge between the side panel and the page
// scripts, and the submission watcher (requirements 5.1, 7).
import { api, fetchFile, pair, saveToken, status, upload } from "./lib/backend.js";
import { attachmentEntry, attachmentFingerprint, forgetAttachment, recordAttachment, shouldAttachFile } from "./lib/attachment-ledger.js";
import "./lib/capture-quality.js";
import { jobPostingKey, scopeLinkedState } from "./lib/page-state.js";

const { pickCapture } = globalThis.__resumeflowCaptureQuality;

const WATCH_MS = 120_000; // watch for the confirmation page for 2 minutes after a submit click

const openOnAction = () => chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
chrome.runtime.onInstalled.addListener(openOnAction);
chrome.runtime.onStartup.addListener(openOnAction);

// ---------------------------------------------------------------- tabs linked to an application

const tabKey = (tabId) => `tab:${tabId}`;
const tabAttachmentsKey = (tabId) => `tab:${tabId}:attachments`;

async function tabState(tabId) {
  return (await chrome.storage.session.get(tabKey(tabId)))[tabKey(tabId)] || null;
}

async function setTabState(tabId, patch) {
  const next = { ...((await tabState(tabId)) || {}), ...patch };
  await chrome.storage.session.set({ [tabKey(tabId)]: next });
  return next;
}

async function tabAttachments(tabId) {
  return (await chrome.storage.session.get(tabAttachmentsKey(tabId)))[tabAttachmentsKey(tabId)] || {};
}

async function setTabAttachments(tabId, ledger) {
  await chrome.storage.session.set({ [tabAttachmentsKey(tabId)]: ledger });
}

async function tabStateForPage(tabId, pageUrl) {
  const linked = await tabState(tabId);
  const scoped = scopeLinkedState(linked, pageUrl);
  if (!scoped.changed) return linked;
  await chrome.storage.session.set({ [tabKey(tabId)]: scoped.state });
  return scoped.state;
}

// The tab's link, and what the side panel showed on it (kept by the panel under panel:<id>).
chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove([tabKey(tabId), tabAttachmentsKey(tabId), `panel:${tabId}`]));

// The application form is often on another site, opened in a new tab from the job page (Apply): that tab gets the
// job page's application.
chrome.tabs.onCreated.addListener(async (tab) => {
  if (tab.openerTabId === undefined) return;
  const opener = await tabState(tab.openerTabId);
  if (!opener?.applicationId && !opener?.jobId) return;
  await setTabState(tab.id, {
    applicationId: opener.applicationId ?? null,
    jobId: opener.jobId ?? null,
    postingKey: opener.postingKey ?? null,
    openedFrom: tab.openerTabId,
  });
});

const hostOf = (url) => {
  try {
    return new URL(url).hostname;
  } catch {
    return "";
  }
};

// ---------------------------------------------------------------- page scripts

async function inject(tabId, file, allFrames = false) {
  await chrome.scripting.executeScript({ target: { tabId, allFrames }, files: Array.isArray(file) ? file : [file] });
}

function run(tabId, func, args = [], { allFrames = false, frameIds } = {}) {
  return chrome.scripting.executeScript({ target: frameIds ? { tabId, frameIds } : { tabId, allFrames }, func, args });
}

async function armWatcher(tabId, armed) {
  await inject(tabId, "src/content/watch.js");
  await run(tabId, (isArmed) => globalThis.__resumeflowWatch.start(isArmed), [armed]);
}

// After a page load in a linked tab (e.g. the confirmation page after Submit), watch again; on the next page of an
// application form being answered, answer it too.
chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  // A selected job can change inside one SPA tab. Clear a link to a different
  // posting before the side panel has a chance to restore it.
  if (info.url) await tabStateForPage(tabId, info.url);
  if (info.status !== "complete") return;
  const state = await tabState(tabId);
  if (!state?.applicationId) return;
  const armed = Boolean(state.armedAt && Date.now() - state.armedAt < WATCH_MS);
  try {
    await armWatcher(tabId, armed);
  } catch {
    // No permission for this site yet: Capture now in the side panel still works after allowing it.
  }
  if (state.autoAnswer && !armed) {
    scheduleAutoAnswer(tabId, 1500);
  }
});

async function captureAndUpload(tabId, applicationId, detectedBy) {
  const tab = await chrome.tabs.get(tabId);
  if (!tab.active) await chrome.tabs.update(tabId, { active: true }); // only the visible tab can be captured
  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
  const blob = await (await fetch(dataUrl)).blob();
  const form = new FormData();
  form.append("image", blob, "confirmation.png");
  form.append("page_url", tab.url || "");
  form.append("page_title", tab.title || "");
  form.append("detected_by", detectedBy);
  form.append("captured_at", new Date().toISOString());
  const result = await upload(`/applications/${applicationId}/submission`, form);
  await setTabState(tabId, { armedAt: null, capturedAt: Date.now(), autoAnswer: false }); // submitted: nothing more to answer
  chrome.notifications.create({
    type: "basic",
    iconUrl: chrome.runtime.getURL("icons/icon128.png"),
    title: "Application submitted",
    message: `Saved the confirmation screen for ${result.candidate_name || "the application"} (${result.company || result.title || "job"}).`,
  });
  chrome.runtime.sendMessage({ type: "submission-captured", tabId, applicationId }).catch(() => {});
  return result;
}

async function onPageMessage(message, sender) {
  const tabId = sender.tab?.id;
  if (tabId === undefined) return;
  const state = await tabState(tabId);
  if (!state?.applicationId) return;
  if (message.type === "submit-clicked") {
    await setTabState(tabId, { armedAt: Date.now() });
  } else if (message.type === "confirmation-detected") {
    if (state.armedAt && Date.now() - state.armedAt < WATCH_MS) {
      await captureAndUpload(tabId, state.applicationId, "auto");
    }
  }
}

// ---------------------------------------------------------------- requests from the side panel and options page

async function fileFor(applicationId, kind) {
  try {
    return await fetchFile(`/applications/${applicationId}/files/${kind}?format=pdf`);
  } catch (error) {
    if (error.status !== 404) throw error;
    return fetchFile(`/applications/${applicationId}/files/${kind}?format=docx`);
  }
}

// ---------------------------------------------------------------- answering application forms

async function scanForm(tabId) {
  await inject(tabId, "src/content/form.js", true);
  const results = await run(tabId, () => globalThis.__resumeflowForm.scan(), [], { allFrames: true });
  return results.flatMap((r) => (r.result || []).map((field) => ({ ...field, field_id: `${r.frameId}:${field.field_id}` })));
}

// Manual and automatic answering can overlap around a fast ATS rerender. Serialize the file decision and reservation for
// a tab so both runs cannot observe an empty ledger and attach the same resume.
const attachmentQueues = new Map();

function withAttachmentLock(tabId, action) {
  const previous = attachmentQueues.get(tabId) || Promise.resolve();
  const current = previous.catch(() => {}).then(action);
  const settled = current.then(
    () => undefined,
    () => undefined,
  );
  attachmentQueues.set(tabId, settled);
  settled.then(() => {
    if (attachmentQueues.get(tabId) === settled) attachmentQueues.delete(tabId);
  });
  return current;
}

function fillForm(tabId, applicationId, answers, options = {}) {
  return withAttachmentLock(tabId, () => fillFormLocked(tabId, applicationId, answers, options));
}

async function fillFormLocked(tabId, applicationId, answers, { keepFilled = false, answerHost = "" } = {}) {
  const byFrame = new Map();
  for (const answer of answers) {
    const [frame, ...rest] = answer.field_id.split(":");
    const list = byFrame.get(Number(frame)) || [];
    list.push({ ...answer, field_id: rest.join(":") });
    byFrame.set(Number(frame), list);
  }
  const files = {};
  const summary = { filled: 0, needs: 0, attached: 0, failed: 0, kept: 0 };
  let attachmentLedger = await tabAttachments(tabId);
  const remember = async (kind, fingerprint, status) => {
    attachmentLedger = recordAttachment(attachmentLedger, { applicationId, answerHost, kind, fingerprint, status });
    await setTabAttachments(tabId, attachmentLedger);
  };
  const forget = async (kind) => {
    attachmentLedger = forgetAttachment(attachmentLedger, applicationId, answerHost, kind);
    await setTabAttachments(tabId, attachmentLedger);
  };
  for (const [frameId, items] of byFrame) {
    const options = { keepFilled };
    const [filled] = await run(tabId, (list, opts) => globalThis.__resumeflowForm.fill(list, opts), [items.filter((i) => i.source !== "file"), options], { frameIds: [frameId] });
    for (const key of ["filled", "needs", "failed", "kept"]) summary[key] += filled?.result?.[key] || 0;
    for (const item of items.filter((candidate) => candidate.source === "file" && candidate.value)) {
      const kind = item.value;
      // Do this before downloading the file: after one attempt, later automatic page loads must not replace it even if
      // the ATS has rebuilt the native input and made it look empty.
      if (!shouldAttachFile(attachmentLedger, { applicationId, answerHost, kind, fingerprint: null, automatic: keepFilled })) {
        summary.kept += 1;
        continue;
      }
      files[kind] ||= await fileFor(applicationId, kind).catch(() => null);
      const file = files[kind];
      if (!file) {
        summary.failed += 1;
        continue;
      }
      const fingerprint = attachmentFingerprint(file);
      if (!shouldAttachFile(attachmentLedger, { applicationId, answerHost, kind, fingerprint, automatic: keepFilled })) {
        summary.kept += 1;
        continue;
      }
      const previousAttachment = attachmentEntry(attachmentLedger, applicationId, answerHost, kind);

      // Reserve before dispatching change: some ATS pages navigate synchronously from that event, destroying the frame
      // before executeScript can return a success result. An explicit manual pass can retry an uncertain attempt.
      await remember(kind, fingerprint, "attempted");
      try {
        const payload = [{ field_id: item.field_id, ...file }];
        const [execution] = await run(tabId, (list, opts) => globalThis.__resumeflowForm.attach(list, opts), [payload, options], { frameIds: [frameId] });
        const attached = execution?.result?.attached || 0;
        const failed = execution?.result?.failed || 0;
        const missing = execution?.result?.missing || 0;
        const kept = execution?.result?.kept || 0;
        summary.attached += attached;
        summary.failed += failed;
        summary.kept += kept;
        if (missing) {
          // The scanned input was replaced before execution. No upload event fired, so a later scan may safely retry.
          if (previousAttachment) {
            await remember(kind, previousAttachment.fingerprint, previousAttachment.status);
          } else {
            await forget(kind);
          }
        } else {
          // No explicit result is intentionally left as attempted: older injected scripts and navigation can both make the
          // result unknowable, and an automatic retry in that state would recreate the replacement loop.
          await remember(kind, fingerprint, attached ? "attached" : failed ? "failed" : "attempted");
        }
      } catch {
        // Keep the pre-dispatch attempt in session storage. A navigation here usually means the ATS accepted the file;
        // retrying automatically would recreate the replacement loop. The user can still retry manually.
        summary.failed += 1;
      }
    }
  }
  return summary;
}

/**
 * Reads the form fields the user can see, gets their answers from the backend and fills them. The first time (from the
 * side panel) it also starts answering what comes next on this site: the next step or tab of the form, a follow-up
 * question, the next page. Those automatic answers only fill empty fields.
 */
async function answerForm(tabId, applicationId, { auto = false } = {}) {
  if (!auto) await setTabState(tabId, { autoRunCount: 0 });
  const scanned = await scanForm(tabId);
  if (!scanned.length && !auto) throw new Error("No form fields found on this page. Open the application form and try again.");
  // Automatic answers ask only about empty fields: what the user typed, or answered before, costs nothing.
  const fields = (auto ? scanned.filter((f) => !f.filled) : scanned).map(({ filled, ...field }) => field);
  const watchSteps = () => run(tabId, () => globalThis.__resumeflowForm.watchSteps(), [], { allFrames: true }).catch(() => {});
  if (!fields.length) {
    await watchSteps(); // a new page with nothing empty yet: its next step is still answered
    return null;
  }
  const tab = await chrome.tabs.get(tabId);
  const result = await api("POST", `/applications/${applicationId}/answers`, { page_url: tab.url, fields });
  const summary = await fillForm(tabId, applicationId, result.answers, { keepFilled: auto, answerHost: hostOf(tab.url) });
  const previous = await tabState(tabId);
  await setTabState(tabId, { applicationId, autoAnswer: previous?.autoAnswer ?? true, answerHost: auto ? previous?.answerHost : hostOf(tab.url) });
  await armWatcher(tabId, false).catch(() => {});
  await watchSteps();
  const labels = Object.fromEntries(fields.map((f) => [f.field_id, f.label || f.placeholder || f.name || "Field"]));
  const answered = { tabId, applicationId, auto, summary, aiRequest: result.ai_request, needs: result.answers.filter((a) => a.source === "needs_input"), labels };
  if (auto) chrome.runtime.sendMessage({ type: "form-answered", ...answered }).catch(() => {});
  return answered;
}

const autoTimers = new Map();
const autoBusy = new Set();

function scheduleAutoAnswer(tabId, delay = 1200) {
  clearTimeout(autoTimers.get(tabId));
  autoTimers.set(
    tabId,
    setTimeout(() => {
      autoTimers.delete(tabId);
      autoAnswer(tabId).catch((error) => console.warn("ResumeFlow: automatic answers failed", error));
    }, delay),
  );
}

async function autoAnswer(tabId) {
  if (autoBusy.has(tabId)) return scheduleAutoAnswer(tabId); // a step changed while answering the previous one
  const state = await tabState(tabId);
  if (!state?.applicationId || !state.autoAnswer) return;
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab || hostOf(tab.url) !== state.answerHost) return; // only on the site where the user started answering
  // Keep the cap in session storage so a reload loop or service-worker restart cannot reset it. An explicit Fill action
  // or linking a different application starts a fresh allowance.
  const runs = Number(state.autoRunCount || 0);
  if (runs >= 30) return;
  await setTabState(tabId, { autoRunCount: runs + 1 });
  autoBusy.add(tabId);
  try {
    await answerForm(tabId, state.applicationId, { auto: true });
  } finally {
    autoBusy.delete(tabId);
  }
}

const handlers = {
  status: () => status(),
  pair: ({ code, baseUrl }) => pair(code, baseUrl),
  saveToken: ({ token, baseUrl }) => saveToken(token, baseUrl),
  api: ({ method, path, body }) => api(method, path, body),
  file: ({ path }) => fetchFile(path),
  tabState: ({ tabId, pageUrl }) => tabStateForPage(tabId, pageUrl),
  linkTab: async ({ tabId, applicationId, jobId }) => {
    const previous = await tabState(tabId);
    const same = Boolean(applicationId) && previous?.applicationId === applicationId;
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    // autoAnswer: unset until the first answers for this application (then on), false once the user turns it off.
    const state = await setTabState(tabId, {
      applicationId: applicationId ?? null,
      jobId: jobId ?? null,
      postingKey: jobPostingKey(tab?.url) ?? null,
      autoAnswer: same ? previous.autoAnswer : undefined,
      autoRunCount: same ? Number(previous?.autoRunCount || 0) : 0,
    });
    if (applicationId) await armWatcher(tabId, false).catch(() => {});
    return state;
  },
  setAutoAnswer: ({ tabId, on }) => setTabState(tabId, { autoAnswer: Boolean(on) }),
  answerForm: ({ tabId, applicationId }) => answerForm(tabId, applicationId),
  capture: async ({ tabId }) => {
    await inject(tabId, ["src/lib/capture-quality.js", "src/content/capture.js"], true);
    const results = await run(tabId, () => globalThis.__resumeflowCapture.capture(), [], { allFrames: true });
    return pickCapture(results);
  },
  captureNow: ({ tabId, applicationId }) => captureAndUpload(tabId, applicationId, "manual"),
  openWorkspace: ({ hash }) => chrome.tabs.create({ url: chrome.runtime.getURL(`workspace/index.html${hash ? `#${hash}` : ""}`) }),
};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.tab && (message?.type === "submit-clicked" || message?.type === "confirmation-detected")) {
    onPageMessage(message, sender).catch((error) => console.warn("ResumeFlow: submission capture failed", error));
    return false;
  }
  if (sender.tab && message?.type === "form-step") {
    scheduleAutoAnswer(sender.tab.id); // new fields on a form being answered
    return false;
  }
  const handler = handlers[message?.type];
  if (!handler) return false;
  Promise.resolve()
    .then(() => handler(message))
    .then(
      (data) => sendResponse({ ok: true, data }),
      (error) => sendResponse({ ok: false, error: { message: error?.message || String(error), code: error?.code, status: error?.status } }),
    );
  return true; // the response is sent asynchronously
});
