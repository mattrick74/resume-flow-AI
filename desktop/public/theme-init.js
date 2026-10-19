// Applies the cached theme before first paint to avoid a light flash.
// A static file rather than an inline script so the CSP can stay `script-src 'self'`.
try {
  var pref = localStorage.getItem("resumeflow.theme") || "system";
  var dark = pref === "dark" || (pref === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  if (dark) document.documentElement.classList.add("dark");
} catch (e) {}
