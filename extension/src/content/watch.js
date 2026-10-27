// Recognizes the confirmation page after the user submits (requirements 7.1). The user's own click on Submit/Apply
// arms the watcher; for 2 minutes after that, a confirmation URL or confirmation text is reported to the service
// worker, which captures the tab. Nothing here clicks or submits anything.
(() => {
  const CONFIRM_TEXT =
    /thank(?:s| you) for (?:applying|your application|your interest and application)|(?:your )?application (?:has been |was )?(?:submitted|received|sent|complete)|we(?:'ve| have) received your application|you(?:'ve| have) (?:successfully )?applied|successfully (?:submitted|applied)|application sent|we received your application/i;
  const CONFIRM_PATH = /(confirmation|thank-?you|thanks|applied|success|submitted|complete)/i;
  const SUBMIT_TEXT = /\b(submit|apply|send application|finish|complete application)\b/i;
  // The step buttons of a multi-step form are often submit buttons too; moving to the next step isn't submitting.
  const STEP_TEXT = /^(?:next|continue|save (?:and|&) continue|save|back|previous|review|add(?: another)?|upload)\b/i;
  const buttonText = (button) => `${button.innerText || button.textContent || ""} ${button.value || ""} ${button.getAttribute("aria-label") || ""}`.trim();
  const WATCH_MS = 120_000;

  function looksConfirmed(doc = document, href = location.href) {
    const text = (doc.body?.innerText || doc.body?.textContent || "").slice(0, 30_000);
    if (CONFIRM_TEXT.test(text)) return true;
    let path = "";
    try {
      const url = new URL(href);
      path = url.pathname + url.search;
    } catch {
      path = href;
    }
    return CONFIRM_PATH.test(path) && /thank|submitted|received|applied/i.test(text);
  }

  if (globalThis.__resumeflowWatch) return;

  let watching = null;
  let reported = false;

  function report() {
    if (reported) return;
    reported = true;
    stop();
    setTimeout(() => chrome.runtime.sendMessage({ type: "confirmation-detected", url: location.href, title: document.title }), 1000); // let the page settle
  }

  function stop() {
    if (!watching) return;
    watching.observer.disconnect();
    clearInterval(watching.interval);
    clearTimeout(watching.timeout);
    watching = null;
  }

  function watch() {
    stop();
    reported = false;
    if (looksConfirmed()) return report();
    const observer = new MutationObserver(() => looksConfirmed() && report());
    observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
    watching = { observer, interval: setInterval(() => looksConfirmed() && report(), 1000), timeout: setTimeout(stop, WATCH_MS) };
  }

  function submitted() {
    chrome.runtime.sendMessage({ type: "submit-clicked", url: location.href });
    watch();
  }

  document.addEventListener(
    "click",
    (event) => {
      const button = event.target?.closest?.("button, input[type='submit'], input[type='button'], a[role='button'], [role='button']");
      if (!button) return;
      const text = buttonText(button);
      if (STEP_TEXT.test(text)) return;
      if (button.type === "submit" || SUBMIT_TEXT.test(text)) submitted();
    },
    true,
  );
  document.addEventListener(
    "submit",
    (event) => {
      if (event.submitter && STEP_TEXT.test(buttonText(event.submitter))) return;
      submitted();
    },
    true,
  );

  globalThis.__resumeflowWatch = {
    /** armed: the user clicked Submit on the previous page of this tab within the last 2 minutes. */
    start(armed) {
      if (armed) watch();
    },
    looksConfirmed,
  };
})();
