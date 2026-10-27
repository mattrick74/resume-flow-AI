// ResumeFlow service worker: the only caller of the local backend, the bridge between the side panel and the page
// scripts, and the submission watcher (requirements 5.1, 7).
import { api, fetchFile, pair, saveToken, status, upload } from "./lib/backend.js";

const WATCH_MS = 120_000; // watch for the confirmation page for 2 minutes after a submit click

const openOnAction = () => chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
chrome.runtime.onInstalled.addListener(openOnAction);
chrome.runtime.onStartup.addListener(openOnAction);

// ---------------------------------------------------------------- tabs linked to an application

const tabKey = (tabId) => `tab:${tabId}`;

async function tabState(tabId) {
  return (await chrome.storage.session.get(tabKey(tabId)))[tabKey(tabId)] || null;
}

async function setTabState(tabId, patch) {
  const next = { ...((await tabState(tabId)) || {}), ...patch };
  await chrome.storage.session.set({ [tabKey(tabId)]: next });
  return next;
}

// The tab's link, and what the side panel showed on it (kept by the panel under panel:<id>).
chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove([tabKey(tabId), `panel:${tabId}`]));

// The application form is often on another site, opened in a new tab from the job page (Apply): that tab gets the
// job page's application.
chrome.tabs.onCreated.addListener(async (tab) => {
  if (tab.openerTabId === undefined) return;
  const opener = await tabState(tab.openerTabId);
  if (!opener?.applicationId && !opener?.jobId) return;
  await setTabState(tab.id, { applicationId: opener.applicationId ?? null, jobId: opener.jobId ?? null, openedFrom: tab.openerTabId });
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
  await chrome.scripting.executeScript({ target: { tabId, allFrames }, files: [file] });
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
    autoRuns.delete(tabId);
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

function pickCapture(results) {
  // The top frame's reading, unless a frame (e.g. an embedded job board) holds a clearly longer description.
  const readings = results.map((r) => r.result).filter(Boolean);
  const top = results.find((r) => r.frameId === 0)?.result || readings[0] || null;
  const longest = readings.reduce((best, r) => ((r.description || "").length > (best?.description || "").length ? r : best), top);
  return longest && top && (longest.description || "").length > 1.5 * (top.description || "").length ? { ...longest, url: top.url, page_title: top.page_title } : top;
}

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

async function fillForm(tabId, applicationId, answers, { keepFilled = false } = {}) {
  const byFrame = new Map();
  for (const answer of answers) {
    const [frame, ...rest] = answer.field_id.split(":");
    const list = byFrame.get(Number(frame)) || [];
    list.push({ ...answer, field_id: rest.join(":") });
    byFrame.set(Number(frame), list);
  }
  const files = {};
  const summary = { filled: 0, needs: 0, attached: 0, failed: 0, kept: 0 };
  for (const [frameId, items] of byFrame) {
    const fileItems = [];
    for (const item of items.filter((i) => i.source === "file" && i.value)) {
      files[item.value] ||= await fileFor(applicationId, item.value).catch(() => null);
      if (files[item.value]) fileItems.push({ field_id: item.field_id, ...files[item.value] });
    }
    const options = { keepFilled };
    const [filled] = await run(tabId, (list, opts) => globalThis.__resumeflowForm.fill(list, opts), [items.filter((i) => i.source !== "file"), options], { frameIds: [frameId] });
    for (const key of ["filled", "needs", "failed", "kept"]) summary[key] += filled?.result?.[key] || 0;
    if (fileItems.length) {
      const [attached] = await run(tabId, (list, opts) => globalThis.__resumeflowForm.attach(list, opts), [fileItems, options], { frameIds: [frameId] });
      summary.attached += attached?.result?.attached || 0;
      summary.failed += attached?.result?.failed || 0;
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
  const summary = await fillForm(tabId, applicationId, result.answers, { keepFilled: auto });
  const previous = await tabState(tabId);
  await setTabState(tabId, { applicationId, autoAnswer: previous?.autoAnswer ?? true, answerHost: auto ? previous?.answerHost : hostOf(tab.url) });
  await armWatcher(tabId, false).catch(() => {});
  await watchSteps();
  const labels = Object.fromEntries(fields.map((f) => [f.field_id, f.label || f.placeholder || f.name || "Field"]));
  const answered = { tabId, applicationId, auto, summary, aiRequest: result.ai_request, needs: result.answers.filter((a) => a.source === "needs_input"), labels };
  if (auto) chrome.runtime.sendMessage({ type: "form-answered", ...answered }).catch(() => {});
  return answered;
}

const autoRuns = new Map(); // tab id -> automatic answers since the page loaded (a cap against loops)
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
  const runs = autoRuns.get(tabId) || 0;
  if (runs >= 30) return;
  autoRuns.set(tabId, runs + 1);
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
  tabState: ({ tabId }) => tabState(tabId),
  linkTab: async ({ tabId, applicationId, jobId }) => {
    const previous = await tabState(tabId);
    const same = Boolean(applicationId) && previous?.applicationId === applicationId;
    // autoAnswer: unset until the first answers for this application (then on), false once the user turns it off.
    const state = await setTabState(tabId, { applicationId: applicationId ?? null, jobId: jobId ?? null, autoAnswer: same ? previous.autoAnswer : undefined });
    if (applicationId) await armWatcher(tabId, false).catch(() => {});
    return state;
  },
  setAutoAnswer: ({ tabId, on }) => setTabState(tabId, { autoAnswer: Boolean(on) }),
  answerForm: ({ tabId, applicationId }) => answerForm(tabId, applicationId),
  capture: async ({ tabId }) => {
    await inject(tabId, "src/content/capture.js", true);
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
