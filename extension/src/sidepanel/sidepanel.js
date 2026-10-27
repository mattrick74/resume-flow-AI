// The side panel next to the job page (requirements 5): capture → save & rank → generate → answer questions →
// confirmation screen. Backend calls go through the service worker. Page data is shown with textContent only.

const $ = (id) => document.getElementById(id);
const DONE = new Set(["EXPORTED", "SUBMITTED", "INTERVIEW", "OFFER", "REJECTED", "FAILED"]);
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

const state = { tab: null, connected: false, capture: null, job: null, application: null, poll: null, fieldLabels: {} };

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
}

// ---------------------------------------------------------------- the active tab

async function loadTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const changed = tab?.id !== state.tab?.id;
  state.tab = tab || null;
  $("tab-title").textContent = tab ? tab.title || tab.url : "No tab";
  if (!changed) return;
  stopPolling();
  state.capture = state.job = state.application = null;
  ["captured", "candidates", "application", "answers"].forEach((id) => show(id, false));
  message("");
  if (!tab || !state.connected) return updateButtons();
  try {
    const linked = await send("tabState", { tabId: tab.id });
    if (linked?.applicationId) await loadApplication(linked.applicationId);
    else if (linked?.jobId) await loadJob(linked.jobId);
  } catch (error) {
    message(error.message);
  }
  await loadApplicationChoices();
  updateButtons();
}

async function loadApplicationChoices() {
  const select = $("application-select");
  select.replaceChildren(el("option", { value: "" }, "—"));
  if (!state.connected) return;
  try {
    const apps = await api("GET", "/applications?limit=25");
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
  const box = $("same-url");
  box.replaceChildren();
  show("same-url", false);
  if (!url) return;
  const { same_url: earlier } = await api("GET", `/jobs/by-url?url=${encodeURIComponent(url)}`);
  renderSameUrl(earlier);
}

function renderSameUrl(earlier) {
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

async function saveJob() {
  const description = $("job-description").value.trim();
  if (description.length < 50) throw new Error("Capture or paste the full job description first.");
  const url = $("job-url").value.trim();
  const job = await api("POST", "/jobs", {
    raw_jd: description,
    job_url: url,
    title: $("job-title").value.trim() || null,
    company: $("job-company").value.trim() || null,
    location: $("job-location").value.trim() || null,
    employment_type: state.capture?.employment_type || null,
    source: "extension",
    site: state.capture?.site || null,
    page_title: state.capture?.page_title || null,
    analyze: true,
  });
  await send("linkTab", { tabId: state.tab.id, jobId: job.id });
  showJob(job);
  renderSameUrl(job.same_url);
}

async function loadJob(jobId) {
  showJob(await api("GET", `/jobs/${jobId}`));
}

function showJob(job) {
  state.job = job;
  show("captured", false);
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

async function loadApplication(applicationId) {
  const app = await api("GET", `/applications/${applicationId}`);
  state.application = app;
  renderApplication(app);
  $("application-select").value = app.id;
  stopPolling();
  if (!DONE.has(app.status)) state.poll = setTimeout(() => loadApplication(applicationId).catch((e) => message(e.message)), 1500);
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

async function answerQuestions() {
  await allowSites();
  const fields = await send("scanForm", { tabId: state.tab.id });
  if (!fields.length) throw new Error("No form fields found on this page. Open the application form and try again.");
  state.fieldLabels = Object.fromEntries(fields.map((f) => [f.field_id, f.label || f.placeholder || f.name || "Field"]));
  const result = await api("POST", `/applications/${state.application.id}/answers`, { page_url: state.tab.url, fields });
  const summary = await send("fillForm", { tabId: state.tab.id, applicationId: state.application.id, answers: result.answers });
  const parts = [`${summary.filled} filled`, summary.attached ? `${summary.attached} file${summary.attached > 1 ? "s" : ""} attached` : null, `${summary.needs + summary.failed} for you`];
  $("answers-summary").textContent = parts.filter(Boolean).join(" · ") + (result.ai_request ? " · 1 AI request" : "");
  renderNeeds(result.answers.filter((a) => a.source === "needs_input"));
  show("answers");
}

function renderNeeds(needs) {
  const list = $("needs-list");
  list.replaceChildren();
  if (!needs.length) list.append(el("p", { class: "muted small" }, "Every field was filled. Check them, then submit on the page."));
  for (const answer of needs) {
    const label = state.fieldLabels[answer.field_id] || answer.field_id;
    const input = el("input", { placeholder: "Your answer (saved for next time)", maxlength: "5000" });
    const save = el(
      "button",
      {
        type: "button",
        onclick: async () => {
          if (!input.value.trim()) return;
          save.disabled = true;
          try {
            await api("PUT", "/answer-bank", { candidate_id: state.application.candidate_id, question: label, answer: input.value.trim() });
            save.textContent = "Saved";
            input.disabled = true;
          } catch (error) {
            save.disabled = false;
            message(error.message);
          }
        },
      },
      "Save",
    );
    list.append(el("div", { class: "item" }, el("strong", { class: "small" }, label), answer.note ? el("span", { class: "muted small" }, answer.note) : null, el("div", { class: "row" }, input, save)));
  }
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
$("retry-status").addEventListener("click", () => refreshStatus().then(loadTab));
$("open-workspace").addEventListener("click", () => send("openWorkspace", { hash: state.application ? `/applications/${state.application.id}` : "/" }).catch((e) => message(e.message)));
$("capture").addEventListener("click", (e) => busy(e.currentTarget, capture));
$("save-job").addEventListener("click", (e) => busy(e.currentTarget, saveJob));
$("generate").addEventListener("click", (e) => busy(e.currentTarget, generate));
$("answer").addEventListener("click", (e) => busy(e.currentTarget, answerQuestions));
$("capture-now").addEventListener("click", (e) => busy(e.currentTarget, captureNow));
$("job-description").addEventListener("input", onDescriptionInput);
$("application-select").addEventListener("change", (e) => useApplication(e.target.value).catch((error) => message(error.message)));

chrome.tabs.onActivated.addListener(() => loadTab());
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (tabId === state.tab?.id && (info.title || info.url)) $("tab-title").textContent = info.title || state.tab.title || info.url;
});
chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "submission-captured" && msg.applicationId === state.application?.id) loadApplication(msg.applicationId).catch(() => {});
});

refreshStatus().then(loadTab);
