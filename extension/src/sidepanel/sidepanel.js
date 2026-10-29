// The side panel next to the job page (requirements 5): capture → save & rank (or save for later) → generate → answer
// questions → confirmation screen. Backend calls go through the service worker. Page data is shown with textContent only.
// One side panel serves every tab of the window, so what each tab showed is kept per tab and put back on return.

import { jobPostingKey, snapshotMatchesPosting } from "../lib/page-state.js";

const $ = (id) => document.getElementById(id);
const DONE = new Set(["EXPORTED", "SUBMITTED", "INTERVIEW", "OFFER", "REJECTED", "FAILED"]);
const ACTIVE = new Set(["queued", "running"]);
const STATUS_LABEL = {
  MATCHED: "Queued",
  GENERATING: "Writing",
  VALIDATING: "Writing",
  AWAITING_REVIEW: "Waiting for approval",
  APPROVED: "Saving files",
  EXPORTED: "Files saved",
  SUBMITTED: "Submitted",
  INTERVIEW: "Interview",
  OFFER: "Offer",
  REJECTED: "Rejected",
  FAILED: "Failed",
};

const state = {
  tab: null,
  connected: false,
  capture: null,
  job: null,
  jobBusy: false,
  application: null,
  poll: null,
  fieldLabels: {},
  needs: [],
  sameUrl: null,
  note: null,
  recentApps: [], // the last 25 applications, for the choice list and the apply-page suggestion
  // Each tab switch starts a new load; answers that arrive for an earlier one are dropped.
  seq: 0,
  // False while a tab's panel is being put back, so a half-restored panel is never saved as that tab's.
  ready: false,
};

async function send(type, payload = {}) {
  const response = await chrome.runtime.sendMessage({ type, ...payload });
  if (!response?.ok) {
    const error = new Error(response?.error?.message || "Something went wrong.");
    Object.assign(error, response?.error || {});
    throw error;
  }
  return response.data;
}
const api = (method, path, body) => send("api", { method, path, body });
const openWorkspace = (hash) => send("openWorkspace", { hash }).catch((e) => message(e.message));

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) if (child !== null && child !== undefined && child !== false) node.append(child instanceof Node ? child : String(child));
  return node;
}

function show(id, visible = true) {
  $(id).hidden = !visible;
}

function message(text, kind = "error") {
  const box = $("page-message");
  box.textContent = text || "";
  box.className = kind === "info" ? "message info" : "message";
  box.hidden = !text;
  state.note = text ? { text, kind } : null;
  remember();
}

async function busy(button, task) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Working…";
  try {
    return await task();
  } catch (error) {
    message(error.message);
    return null;
  } finally {
    button.disabled = false;
    button.textContent = label;
    updateButtons();
    remember();
  }
}

/** Access to job pages, asked once (Chrome shows the prompt only the first time). */
async function allowSites() {
  const granted = await chrome.permissions.request({ origins: ["<all_urls>"] });
  if (!granted) throw new Error("ResumeFlow needs access to the job page to read it. Click again and choose Allow.");
}

// ---------------------------------------------------------------- connection

async function refreshStatus() {
  const pill = $("status");
  try {
    const status = await send("status");
    state.connected = status.running && status.paired;
    pill.textContent = state.connected ? `Connected · v${status.version}` : status.running ? "Not paired" : "Backend off";
    pill.className = `pill ${state.connected ? "ok" : "bad"}`;
    $("connection-message").textContent = status.message || "";
    show("connection", !state.connected);
  } catch (error) {
    state.connected = false;
    pill.textContent = "Error";
    pill.className = "pill bad";
    $("connection-message").textContent = error.message;
    show("connection", true);
  }
  updateButtons();
}

function updateButtons() {
  const ready = state.connected && state.tab;
  $("capture").disabled = !ready;
  $("answer").disabled = !(ready && state.application);
  $("capture-now").disabled = !(ready && state.application);
  $("generate-one").disabled = $("rank-now").disabled = !(ready && state.job) || state.jobBusy;
  $("generate-saved").disabled = !state.connected;
}

// ---------------------------------------------------------------- what each tab showed

const panelKey = (tabId) => `panel:${tabId}`;
const panels = new Map(); // tab id -> snapshot; also kept in session storage for when the panel is reopened
let rememberTimer = null;

function snapshot() {
  const value = (id) => $(id).value;
  const drafts = Object.fromEntries([...$("needs-list").querySelectorAll("input[data-field]")].map((i) => [i.dataset.field, { value: i.value, saved: i.disabled }]));
  return {
    postingKey: jobPostingKey(state.tab?.url),
    captured: !$("captured").hidden,
    capture: state.capture,
    form: { title: value("job-title"), company: value("job-company"), location: value("job-location"), url: value("job-url"), description: value("job-description") },
    sameUrl: state.sameUrl,
    candidate: document.querySelector("input[name='candidate']:checked")?.value || null,
    overrideReason: value("override-reason"),
    answers: $("answers").hidden ? null : { summary: $("answers-summary").textContent, needs: state.needs, fieldLabels: state.fieldLabels, drafts },
    note: state.note,
  };
}

function rememberNow() {
  clearTimeout(rememberTimer);
  rememberTimer = null;
  if (!state.tab || !state.ready) return;
  const tabId = state.tab.id;
  const snap = snapshot();
  panels.set(tabId, snap);
  chrome.storage.session.set({ [panelKey(tabId)]: snap }).catch(() => {});
}

function remember() {
  clearTimeout(rememberTimer);
  rememberTimer = setTimeout(rememberNow, 300);
}

async function rememberedPanel(tabId, pageUrl) {
  const snap = panels.has(tabId) ? panels.get(tabId) : (await chrome.storage.session.get(panelKey(tabId)))[panelKey(tabId)] || null;
  return snap && snapshotMatchesPosting(snap.postingKey, pageUrl) ? snap : null;
}

/** Puts back what the user had on this tab that the backend doesn't hold: an unsaved capture, a chosen candidate, form answers. */
function applySnapshot(snap) {
  if (snap.captured && snap.form) {
    state.capture = snap.capture || null;
    $("job-title").value = snap.form.title || "";
    $("job-company").value = snap.form.company || "";
    $("job-location").value = snap.form.location || "";
    $("job-url").value = snap.form.url || "";
    $("job-description").value = snap.form.description || "";
    $("read-by").textContent = snap.capture?.read_by ? `read from ${snap.capture.read_by}` : "";
    onDescriptionInput();
    show("captured");
    renderSameUrl(snap.sameUrl);
  }
  if (snap.candidate && !$("candidates").hidden) {
    const input = document.getElementById(`candidate-${snap.candidate}`);
    if (input) input.checked = true;
    $("override-reason").value = snap.overrideReason || "";
    onCandidateChange();
  }
  if (snap.answers && state.application) {
    state.fieldLabels = snap.answers.fieldLabels || {};
    $("answers-summary").textContent = snap.answers.summary || "";
    renderNeeds(snap.answers.needs || [], snap.answers.drafts || {});
    show("answers");
  }
  // An error stays until dealt with; an info note ("Saved for later.") is out of date by the time the user is back.
  if (snap.note?.text && snap.note.kind !== "info") message(snap.note.text, snap.note.kind);
}

// ---------------------------------------------------------------- the active tab

async function loadTab(force = false) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const changed = tab?.id !== state.tab?.id;
  if (changed || force) rememberNow(); // what the previous tab showed, before it's cleared
  state.tab = tab || null;
  $("tab-title").textContent = tab ? tab.title || tab.url : "No tab";
  if (!changed && !force) return;
  const seq = ++state.seq;
  state.ready = false;
  stopPolling();
  state.capture = state.job = state.application = state.sameUrl = null;
  state.jobBusy = false;
  state.needs = [];
  state.fieldLabels = {};
  $("override-reason").value = "";
  ["captured", "saved-job", "candidates", "application", "answers", "suggest"].forEach((id) => show(id, false));
  message("");
  if (!tab || !state.connected) {
    state.ready = true;
    return updateButtons();
  }
  try {
    const [linked, snap] = await Promise.all([send("tabState", { tabId: tab.id, pageUrl: tab.url }), rememberedPanel(tab.id, tab.url)]);
    if (seq !== state.seq) return;
    $("auto-answer").checked = linked?.autoAnswer !== false;
    if (linked?.applicationId) await loadApplication(linked.applicationId, seq);
    else if (linked?.jobId) await loadJob(linked.jobId, seq);
    if (seq !== state.seq) return;
    if (snap) applySnapshot(snap);
    else if (linked?.openedFrom && linked.applicationId) message("Opened from the job page: this tab uses the same application.", "info");
  } catch (error) {
    if (seq === state.seq) message(error.message);
  } finally {
    if (seq === state.seq) state.ready = true;
  }
  await Promise.all([loadApplicationChoices(), refreshSaved()]);
  if (seq === state.seq) suggestApplication();
  updateButtons();
}

async function loadApplicationChoices() {
  const select = $("application-select");
  select.replaceChildren(el("option", { value: "" }, "—"));
  state.recentApps = [];
  if (!state.connected) return;
  try {
    const response = await api("GET", "/applications?limit=25");
    const apps = Array.isArray(response) ? response : response.items ?? [];
    state.recentApps = apps;
    for (const app of apps) {
      const date = String(app.application_date || "").slice(5);
      select.append(el("option", { value: app.id }, `${date} · ${app.candidate_name} · ${app.company || app.title || "Job"}`));
    }
    select.value = state.application?.id || "";
  } catch {
    // the list is a convenience; the rest of the panel still works
  }
}

// ---------------------------------------------------------------- capture and save

async function capture() {
  await allowSites();
  const data = await send("capture", { tabId: state.tab.id });
  if (!data) throw new Error("Couldn't read this page. Select the job description and capture again.");
  state.capture = data;
  $("job-title").value = data.title || "";
  $("job-company").value = data.company || "";
  $("job-location").value = data.location || "";
  $("job-url").value = data.url || "";
  $("job-description").value = data.description || "";
  $("read-by").textContent = `read from ${data.read_by}`;
  onDescriptionInput();
  show("captured");
  await showSameUrl(data.url);
  message("");
}

function onDescriptionInput() {
  const length = $("job-description").value.trim().length;
  $("jd-count").textContent = `${length.toLocaleString()} characters`;
  show("jd-warning", length < 300);
}

async function showSameUrl(url) {
  renderSameUrl(null);
  if (!url) return;
  const { same_url: earlier } = await api("GET", `/jobs/by-url?url=${encodeURIComponent(url)}`);
  renderSameUrl(earlier);
}

function renderSameUrl(earlier) {
  state.sameUrl = earlier || null;
  const box = $("same-url");
  box.replaceChildren();
  const apps = (earlier || []).flatMap((job) => job.applications.map((a) => ({ ...a, job })));
  if (!earlier?.length) return show("same-url", false);
  box.append(el("strong", {}, apps.length ? "Generated before for this posting:" : "This posting was saved before."));
  for (const app of apps.slice(0, 4)) {
    box.append(
      el(
        "div",
        { class: "row" },
        el("span", {}, `${app.application_date} · ${app.candidate_name} · ${STATUS_LABEL[app.status] || app.status}`),
        el("button", { type: "button", class: "link", onclick: () => useApplication(app.application_id) }, "Open"),
      ),
    );
  }
  show("same-url");
}

/** The captured job as the user checked it. */
function jobBody() {
  const description = $("job-description").value.trim();
  if (description.length < 50) throw new Error("Capture or paste the full job description first.");
  return {
    raw_jd: description,
    job_url: $("job-url").value.trim(),
    title: $("job-title").value.trim() || null,
    company: $("job-company").value.trim() || null,
    location: $("job-location").value.trim() || null,
    employment_type: state.capture?.employment_type || null,
    source: "extension",
    site: state.capture?.site || null,
    page_title: state.capture?.page_title || null,
  };
}

async function saveJob() {
  const job = await api("POST", "/jobs", { ...jobBody(), analyze: true });
  await send("linkTab", { tabId: state.tab.id, jobId: job.id });
  showJob(job);
  renderSameUrl(job.same_url);
}

/** Saved without analysis or ranking: generated later on its own, or with every saved job. */
async function saveLater() {
  const job = await api("POST", "/jobs", { ...jobBody(), analyze: false });
  await send("linkTab", { tabId: state.tab.id, jobId: job.id });
  showSavedJob(job);
  await refreshSaved();
  message("Saved for later.", "info");
}

async function loadJob(jobId, seq = state.seq) {
  const job = await api("GET", `/jobs/${jobId}`);
  if (seq !== state.seq) return;
  const latest = job.applications?.[0]; // newest first
  if (latest) {
    // Generated since it was saved (for example with all saved jobs): show its application.
    await send("linkTab", { tabId: state.tab.id, applicationId: latest.id, jobId: job.id });
    return loadApplication(latest.id, seq);
  }
  if (ACTIVE.has(job.processing?.status) || !job.requirements_source) return showSavedJob(job, seq);
  showJob(job);
  if (job.processing?.status === "failed" && job.processing.error) message(job.processing.error);
}

function showSavedJob(job, seq = state.seq) {
  state.job = job;
  stopPolling();
  const active = ACTIVE.has(job.processing?.status);
  state.jobBusy = active;
  show("captured", false);
  show("candidates", false);
  $("saved-job-title").textContent = [job.title, job.company].filter(Boolean).join(" · ") || job.job_url || "Job";
  $("saved-job-state").textContent = active
    ? `Generating in the background${job.processing.progress ? ` · ${job.processing.progress}%` : ""}…`
    : job.processing?.status === "failed"
      ? `Generation failed: ${job.processing.error || "unknown error"}. Try again, or choose the candidate yourself.`
      : "Generate it now on its own, or later together with every saved job.";
  show("saved-job");
  updateButtons();
  if (active) state.poll = setTimeout(() => loadJob(job.id, seq).catch((e) => message(e.message)), 2000);
}

/** This job alone, in the background: analysis, ranking, and the resume and cover letter for the top candidate. */
async function generateOne() {
  const result = await api("POST", "/jobs/process", { job_ids: [state.job.id] });
  if (!result.queued.length) throw new Error(result.skipped[0]?.reason || "This job couldn't be queued.");
  await loadJob(state.job.id);
  await refreshSaved();
}

/** Analyze and rank now, to choose the candidate yourself. */
async function rankNow() {
  showJob(await api("POST", `/jobs/${state.job.id}/match`));
  await refreshSaved();
}

function showJob(job) {
  state.job = job;
  state.jobBusy = false;
  show("captured", false);
  show("saved-job", false);
  $("analysis-source").textContent = job.requirements_source ? `analyzed: ${job.requirements_source === "heuristic" ? "locally" : job.requirements_source}` : "";
  const warnings = $("job-warnings");
  warnings.replaceChildren(...(job.warnings || []).map((w) => el("p", { class: "note small" }, w.message)));
  const list = $("match-list");
  list.replaceChildren();
  const matches = (job.matches || []).filter((m) => !m.details?.excluded).sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99) || (b.score ?? 0) - (a.score ?? 0));
  if (!matches.length) list.append(el("p", { class: "muted" }, "No ready candidate matches this job. Add or finish candidates in the workspace."));
  for (const match of matches.slice(0, 8)) {
    const missing = match.details?.components?.mandatory_skills?.missing || [];
    const id = `candidate-${match.candidate_id}`;
    list.append(
      el(
        "label",
        { class: "item choice", for: id },
        el("input", { type: "radio", name: "candidate", id, value: match.candidate_id, checked: match.rank === 1, onchange: onCandidateChange }),
        el(
          "span",
          {},
          el("strong", {}, match.candidate_name),
          match.rank === 1 ? el("span", { class: "muted small" }, "  · best match") : null,
          missing.length ? el("div", { class: "muted small" }, `Missing: ${missing.slice(0, 5).join(", ")}`) : null,
        ),
        el("span", { class: "score" }, match.score == null ? "—" : Math.round(match.score)),
      ),
    );
  }
  $("generate").disabled = !matches.length;
  onCandidateChange();
  show("candidates");
  updateButtons();
}

function selectedMatch() {
  const id = document.querySelector("input[name='candidate']:checked")?.value;
  return (state.job?.matches || []).find((m) => m.candidate_id === id) || null;
}

function onCandidateChange() {
  const match = selectedMatch();
  show("override-wrap", Boolean(match && match.rank !== 1));
}

async function generate() {
  const match = selectedMatch();
  if (!match) throw new Error("Choose a candidate first.");
  const reason = $("override-reason").value.trim();
  if (match.rank !== 1 && !reason) throw new Error("Say why you're choosing this candidate instead of the top match.");
  const app = await api("POST", "/applications", { job_id: state.job.id, candidate_id: match.candidate_id, override_reason: match.rank === 1 ? null : reason });
  await send("linkTab", { tabId: state.tab.id, applicationId: app.id, jobId: state.job.id });
  show("candidates", false);
  await loadApplication(app.id);
  await loadApplicationChoices();
}

// ---------------------------------------------------------------- saved for later

async function refreshSaved() {
  if (!state.connected) return show("saved-jobs", false);
  try {
    const count = (await api("GET", "/dashboard/summary")).saved_jobs || 0;
    $("saved-count").textContent = `${count} saved job${count === 1 ? "" : "s"} waiting to be generated.`;
    show("saved-jobs", count > 0);
  } catch {
    show("saved-jobs", false);
  }
}

/** Every saved job at once, in the background: each is ranked and its files are saved as it finishes. */
async function generateAllSaved() {
  const result = await api("POST", "/jobs/process", { all_saved: true });
  const count = result.queued.length;
  message(`${count} saved job${count === 1 ? "" : "s"} queued. Each gets its resume and cover letter as it finishes; follow them in the workspace.`, "info");
  if (state.job && result.queued.includes(state.job.id)) await loadJob(state.job.id);
  await refreshSaved();
}

// ---------------------------------------------------------------- the application

async function useApplication(applicationId) {
  if (!applicationId) {
    await send("linkTab", { tabId: state.tab.id, applicationId: null });
    state.application = null;
    show("application", false);
    return updateButtons();
  }
  await allowSites().catch(() => {});
  await send("linkTab", { tabId: state.tab.id, applicationId });
  await loadApplication(applicationId);
}

async function loadApplication(applicationId, seq = state.seq) {
  const app = await api("GET", `/applications/${applicationId}`);
  if (seq !== state.seq) return;
  state.application = app;
  state.jobBusy = false;
  show("saved-job", false);
  renderApplication(app);
  $("application-select").value = app.id;
  stopPolling();
  if (!DONE.has(app.status)) state.poll = setTimeout(() => loadApplication(applicationId, seq).catch((e) => message(e.message)), 1500);
  updateButtons();
}

function stopPolling() {
  clearTimeout(state.poll);
  state.poll = null;
}

function renderApplication(app) {
  $("app-heading").textContent = `${app.candidate_name}`;
  $("app-subtitle").textContent = [app.title, app.company].filter(Boolean).join(" · ");
  const pill = $("app-status");
  pill.textContent = STATUS_LABEL[app.status] || app.status;
  pill.className = `pill ${app.status === "FAILED" ? "bad" : DONE.has(app.status) ? "ok" : ""}`;
  const active = (app.processing || []).find((p) => p.status === "queued" || p.status === "running");
  $("app-progress").hidden = !active;
  if (active) $("app-progress").value = active.progress || 5;
  $("app-error").textContent = app.error || "";
  show("app-error", Boolean(app.error));

  const files = $("files");
  files.replaceChildren();
  const latest = new Map();
  for (const file of (app.files || []).filter((f) => f.exists && ["resume", "cover_letter"].includes(f.type))) {
    const key = `${file.type}:${file.format}`;
    if (!latest.has(key) || latest.get(key).version < file.version) latest.set(key, file);
  }
  for (const file of latest.values()) {
    const name = file.path.split(/[\\/]/).pop();
    files.append(
      el(
        "div",
        { class: "item" },
        el("strong", {}, name),
        el(
          "div",
          { class: "row" },
          el("button", { type: "button", onclick: () => openPath(file.path, false) }, "Open"),
          el("button", { type: "button", class: "ghost", onclick: () => openPath(file.path, true) }, "Show in folder"),
          el("button", { type: "button", class: "ghost", onclick: () => download(app.id, file) }, "Download"),
        ),
      ),
    );
  }
  const shots = (app.files || []).filter((f) => f.type === "submission_screenshot" && f.exists);
  const submission = $("submission");
  submission.replaceChildren();
  if (shots.length || app.submitted_at) {
    submission.append(el("strong", {}, app.status === "SUBMITTED" ? "Submitted" : "Confirmation captured"));
    for (const shot of shots) {
      submission.append(el("div", { class: "row" }, el("span", { class: "small" }, shot.path.split(/[\\/]/).pop()), el("button", { type: "button", class: "link", onclick: () => openPath(shot.path, false) }, "View")));
    }
  } else if (DONE.has(app.status) && app.status !== "FAILED") {
    submission.append(el("span", { class: "small" }, "After you submit on the job site, the confirmation screen is saved here automatically. Use Capture now if it isn't."));
  }
  show("submission", submission.childElementCount > 0);
  show("application");
}

async function openPath(path, reveal) {
  try {
    await api("POST", "/system/open", { path, reveal });
  } catch (error) {
    message(error.message);
  }
}

async function download(applicationId, file) {
  try {
    const data = await send("file", { path: `/applications/${applicationId}/files/${file.type}?format=${file.format}` });
    const bytes = Uint8Array.from(atob(data.base64), (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: data.mime }));
    el("a", { href: url, download: data.filename }).click();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  } catch (error) {
    message(error.message);
  }
}

// ---------------------------------------------------------------- answering the form

/** The service worker reads the visible fields, gets the answers and fills them; from then on it also answers each new
 * step of the form on this site by itself (form-answered messages below). */
async function answerQuestions() {
  await allowSites();
  renderAnswered(await send("answerForm", { tabId: state.tab.id, applicationId: state.application.id }));
  $("auto-answer").checked = (await send("tabState", { tabId: state.tab.id }))?.autoAnswer !== false;
}

function answersSummary(result) {
  const s = result.summary;
  const parts = [`${s.filled} filled`, s.kept ? `${s.kept} kept` : null, s.attached ? `${s.attached} file${s.attached > 1 ? "s" : ""} attached` : null, `${s.needs + s.failed} for you`];
  return (result.auto ? "Next step: " : "") + parts.filter(Boolean).join(" · ") + (result.aiRequest ? " · 1 AI request" : "");
}

function renderAnswered(result) {
  state.fieldLabels = { ...state.fieldLabels, ...result.labels };
  $("answers-summary").textContent = answersSummary(result);
  renderNeeds(result.needs);
  show("answers");
}

/** A step answered on a tab the panel isn't showing: what the panel shows when the user is back there. */
function rememberAnswers(tabId, result) {
  const snap = panels.get(tabId);
  if (!snap) return;
  snap.answers = { summary: answersSummary(result), needs: result.needs, fieldLabels: { ...(snap.answers?.fieldLabels || {}), ...result.labels }, drafts: {} };
  chrome.storage.session.set({ [panelKey(tabId)]: snap }).catch(() => {});
}

// ---------------------------------------------------------------- an apply page in a tab of its own

const CLOSED = new Set(["SUBMITTED", "INTERVIEW", "OFFER", "REJECTED", "FAILED"]);
const COMPANY_FILLER = new Set(["inc", "llc", "ltd", "corp", "corporation", "company", "the", "group", "holdings", "technologies", "technology", "solutions", "services", "systems", "global", "international", "labs"]);

/** "Acme Data Co" -> ["acmedata", "acme"]: matched against the page address and title with everything else removed. */
function companyKeys(name) {
  const words = (name || "").toLowerCase().normalize("NFKD").replace(/[^a-z0-9 ]+/g, " ").split(/\s+/).filter((w) => w.length >= 3 && !COMPANY_FILLER.has(w));
  return words.length ? [words.join(""), ...(words[0].length >= 4 ? [words[0]] : [])] : [];
}

/** A tab with nothing linked whose address or title names the company of a recent open application (the company's
 * own careers site, Workday, Greenhouse...): offer that application. */
function suggestApplication() {
  const box = $("suggest");
  box.replaceChildren();
  show("suggest", false);
  if (!state.tab || state.application || state.job || !$("captured").hidden) return;
  const page = `${state.tab.url || ""} ${state.tab.title || ""}`.toLowerCase().replace(/[^a-z0-9]+/g, "");
  const app = state.recentApps.find((a) => !CLOSED.has(a.status) && companyKeys(a.company).some((key) => page.includes(key)));
  if (!app) return;
  const use = () => useApplication(app.id).then(() => show("suggest", false), (error) => message(error.message));
  box.append(el("span", {}, `Applying to ${app.company}? `), el("button", { type: "button", class: "link", onclick: use }, `Use ${app.candidate_name}'s application`));
  show("suggest");
}

function renderNeeds(needs, drafts = {}) {
  state.needs = needs;
  const list = $("needs-list");
  list.replaceChildren();
  if (!needs.length) list.append(el("p", { class: "muted small" }, "Every field was filled. Check them, then submit on the page."));
  for (const answer of needs) {
    const label = state.fieldLabels[answer.field_id] || answer.field_id;
    const draft = drafts[answer.field_id];
    const input = el("input", { placeholder: "Your answer (saved for next time)", maxlength: "5000", "data-field": answer.field_id, value: draft?.value || null, disabled: draft?.saved });
    const save = el(
      "button",
      {
        type: "button",
        disabled: draft?.saved,
        onclick: async () => {
          if (!input.value.trim()) return;
          save.disabled = true;
          try {
            await api("PUT", "/answer-bank", { candidate_id: state.application.candidate_id, question: label, answer: input.value.trim() });
            save.textContent = "Saved";
            input.disabled = true;
            remember();
          } catch (error) {
            save.disabled = false;
            message(error.message);
          }
        },
      },
      draft?.saved ? "Saved" : "Save",
    );
    list.append(el("div", { class: "item" }, el("strong", { class: "small" }, label), answer.note ? el("span", { class: "muted small" }, answer.note) : null, el("div", { class: "row" }, input, save)));
  }
  remember();
}

async function captureNow() {
  await allowSites();
  const app = await send("captureNow", { tabId: state.tab.id, applicationId: state.application.id });
  state.application = app;
  renderApplication(app);
  message("Confirmation screen saved.", "info");
}

// ---------------------------------------------------------------- wiring

$("open-options").addEventListener("click", () => chrome.runtime.openOptionsPage());
$("retry-status").addEventListener("click", () => refreshStatus().then(() => loadTab(true)));
$("open-workspace").addEventListener("click", () => openWorkspace(state.application ? `/applications/${state.application.id}` : "/"));
$("capture").addEventListener("click", (e) => busy(e.currentTarget, capture));
$("save-job").addEventListener("click", (e) => busy(e.currentTarget, saveJob));
$("save-later").addEventListener("click", (e) => busy(e.currentTarget, saveLater));
$("generate-one").addEventListener("click", (e) => busy(e.currentTarget, generateOne));
$("rank-now").addEventListener("click", (e) => busy(e.currentTarget, rankNow));
$("generate-saved").addEventListener("click", (e) => busy(e.currentTarget, generateAllSaved));
$("view-saved").addEventListener("click", () => openWorkspace("/jobs"));
$("generate").addEventListener("click", (e) => busy(e.currentTarget, generate));
$("answer").addEventListener("click", (e) => busy(e.currentTarget, answerQuestions));
$("capture-now").addEventListener("click", (e) => busy(e.currentTarget, captureNow));
$("edit-notes").addEventListener("click", () => state.application && openWorkspace(`/candidates/${state.application.candidate_id}?tab=answer-notes`));
$("job-description").addEventListener("input", onDescriptionInput);
$("application-select").addEventListener("change", (e) => useApplication(e.target.value).catch((error) => message(error.message)));
// Typing in the captured job, the override reason or an answer is kept for this tab.
$("app").addEventListener("input", remember);
$("app").addEventListener("change", remember);
document.addEventListener("visibilitychange", () => document.visibilityState === "hidden" && rememberNow());

chrome.tabs.onActivated.addListener(() => loadTab());
chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (tabId !== state.tab?.id) return;
  if (info.title || info.url) $("tab-title").textContent = info.title || tab?.title || info.url || state.tab.title;
  if (!info.url || info.url === state.tab.url) return;
  // Save the old posting under its old identity before replacing the tab URL.
  // Marking the panel unready also prevents loadTab(force) from re-saving the
  // still-visible old UI under the new posting's identity.
  rememberNow();
  state.ready = false;
  state.seq += 1; // invalidate any load that started for the previous posting
  state.tab = { ...state.tab, ...tab, url: info.url };
  void loadTab(true);
});
chrome.tabs.onRemoved.addListener((tabId) => panels.delete(tabId));
chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "submission-captured" && msg.applicationId === state.application?.id) loadApplication(msg.applicationId).catch(() => {});
  if (msg?.type === "form-answered") {
    if (msg.tabId === state.tab?.id && msg.applicationId === state.application?.id) renderAnswered(msg);
    else rememberAnswers(msg.tabId, msg);
  }
});
$("auto-answer").addEventListener("change", (e) => state.tab && send("setAutoAnswer", { tabId: state.tab.id, on: e.target.checked }).catch((error) => message(error.message)));

refreshStatus().then(() => loadTab());
