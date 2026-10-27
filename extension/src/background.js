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

chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove(tabKey(tabId)));

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

// After a page load in a linked tab (e.g. the confirmation page after Submit), watch again.
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
  await setTabState(tabId, { armedAt: null, capturedAt: Date.now() });
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

const handlers = {
  status: () => status(),
  pair: ({ code, baseUrl }) => pair(code, baseUrl),
  saveToken: ({ token, baseUrl }) => saveToken(token, baseUrl),
  api: ({ method, path, body }) => api(method, path, body),
  file: ({ path }) => fetchFile(path),
  tabState: ({ tabId }) => tabState(tabId),
  linkTab: async ({ tabId, applicationId, jobId }) => {
    const state = await setTabState(tabId, { applicationId: applicationId ?? null, jobId: jobId ?? null });
    if (applicationId) await armWatcher(tabId, false).catch(() => {});
    return state;
  },
  capture: async ({ tabId }) => {
    await inject(tabId, "src/content/capture.js", true);
    const results = await run(tabId, () => globalThis.__resumeflowCapture.capture(), [], { allFrames: true });
    return pickCapture(results);
  },
  scanForm: async ({ tabId }) => {
    await inject(tabId, "src/content/form.js", true);
    const results = await run(tabId, () => globalThis.__resumeflowForm.scan(), [], { allFrames: true });
    return results.flatMap((r) => (r.result || []).map((field) => ({ ...field, field_id: `${r.frameId}:${field.field_id}` })));
  },
  fillForm: async ({ tabId, applicationId, answers }) => {
    const byFrame = new Map();
    for (const answer of answers) {
      const [frame, ...rest] = answer.field_id.split(":");
      const list = byFrame.get(Number(frame)) || [];
      list.push({ ...answer, field_id: rest.join(":") });
      byFrame.set(Number(frame), list);
    }
    const files = {};
    const summary = { filled: 0, needs: 0, attached: 0, failed: 0 };
    for (const [frameId, items] of byFrame) {
      const fileItems = [];
      for (const item of items.filter((i) => i.source === "file" && i.value)) {
        files[item.value] ||= await fileFor(applicationId, item.value).catch(() => null);
        if (files[item.value]) fileItems.push({ field_id: item.field_id, ...files[item.value] });
      }
      const [filled] = await run(tabId, (list) => globalThis.__resumeflowForm.fill(list), [items.filter((i) => i.source !== "file")], { frameIds: [frameId] });
      for (const key of ["filled", "needs", "failed"]) summary[key] += filled?.result?.[key] || 0;
      if (fileItems.length) {
        const [attached] = await run(tabId, (list) => globalThis.__resumeflowForm.attach(list), [fileItems], { frameIds: [frameId] });
        summary.attached += attached?.result?.attached || 0;
        summary.failed += attached?.result?.failed || 0;
      }
    }
    await setTabState(tabId, { applicationId });
    await armWatcher(tabId, false).catch(() => {});
    return summary;
  },
  captureNow: ({ tabId, applicationId }) => captureAndUpload(tabId, applicationId, "manual"),
  openWorkspace: ({ hash }) => chrome.tabs.create({ url: chrome.runtime.getURL(`workspace/index.html${hash ? `#${hash}` : ""}`) }),
};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.tab && (message?.type === "submit-clicked" || message?.type === "confirmation-detected")) {
    onPageMessage(message, sender).catch((error) => console.warn("ResumeFlow: submission capture failed", error));
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
