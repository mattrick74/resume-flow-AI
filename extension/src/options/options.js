const $ = (id) => document.getElementById(id);
const DEFAULT_BACKEND_URL = "http://127.0.0.1:8765";

async function send(type, payload = {}) {
  const response = await chrome.runtime.sendMessage({ type, ...payload });
  if (!response?.ok) throw new Error(response?.error?.message || "Something went wrong.");
  return response.data;
}

function showStatus(status) {
  const pill = $("status");
  const ok = status.running && status.paired;
  pill.textContent = ok ? `Connected · v${status.version}` : status.running ? "Running, not paired" : "Backend not running";
  pill.className = `pill ${ok ? "ok" : "bad"}`;
  note(ok ? "" : status.message || "");
}

function note(text, kind = "error") {
  const box = $("pair-message");
  box.textContent = text;
  box.className = kind === "info" ? "message info" : "message";
  box.hidden = !text;
}

async function refresh() {
  const { baseUrl } = await chrome.storage.local.get("baseUrl");
  $("base-url").value = baseUrl || DEFAULT_BACKEND_URL;
  showStatus(await send("status"));
  const allowed = await chrome.permissions.contains({ origins: ["<all_urls>"] });
  $("allow-state").textContent = allowed ? "Allowed" : "Not allowed yet";
}

$("pair").addEventListener("click", async () => {
  try {
    const status = await send("pair", { code: $("code").value, baseUrl: $("base-url").value.trim() || DEFAULT_BACKEND_URL });
    showStatus(status);
    if (status.paired) note("Paired. You can close this page.", "info");
    $("code").value = "";
  } catch (error) {
    note(error.message);
  }
});

$("save-token").addEventListener("click", async () => {
  try {
    showStatus(await send("saveToken", { token: $("token").value, baseUrl: $("base-url").value.trim() || DEFAULT_BACKEND_URL }));
    $("token").value = "";
  } catch (error) {
    note(error.message);
  }
});

$("base-url").addEventListener("change", async () => {
  await chrome.storage.local.set({ baseUrl: $("base-url").value.trim() || DEFAULT_BACKEND_URL });
  showStatus(await send("status"));
});

$("allow").addEventListener("click", async () => {
  const granted = await chrome.permissions.request({ origins: ["<all_urls>"] });
  $("allow-state").textContent = granted ? "Allowed" : "Not allowed";
});

$("workspace").addEventListener("click", () => send("openWorkspace", { hash: "/" }));

refresh().catch((error) => note(error.message));
