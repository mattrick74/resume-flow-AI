// Reads and fills application-form fields (requirements EXT-08 to EXT-10). Never clicks Submit or Apply, never ticks
// consent, certification or terms boxes, never touches CAPTCHAs (EXT-09). Runs in every frame of the tab.
(() => {
  if (globalThis.__resumeflowForm) return;

  const ATTR = "data-resumeflow-id";
  const SELECTOR = "input, textarea, select, [contenteditable='true'], [role='textbox']";
  // Ids stay unique for the life of the page: the steps of a multi-step form often keep earlier steps in the page, hidden,
  // and a reused id would fill the hidden field instead of the visible one.
  const LOAD = Math.random().toString(36).slice(2, 6);
  let counter = 0;
  const nextId = () => `rf-${LOAD}-${++counter}`;
  const CONSENT = /\bi (?:hereby )?(?:certify|attest|acknowledge|agree|understand|confirm|consent|authori[sz]e)\b|\bconsent\b|terms (?:and|&) conditions|terms of (?:use|service)|privacy (?:policy|notice)|background (?:check|screening)|\bsignature\b|true and (?:complete|correct|accurate)|\bagree\b/i;
  const SKIP_TYPES = new Set(["hidden", "submit", "button", "image", "reset", "password", "search"]);
  const CAPTCHA = /captcha|recaptcha|hcaptcha|turnstile/i;
  const clean = (value) => (value || "").replace(/\s+/g, " ").replace(/\s*\*\s*$/, "").trim();

  /** Every element in the document, including open shadow roots. */
  function allRoots(root = document, found = [document]) {
    for (const element of root.querySelectorAll("*")) {
      if (element.shadowRoot) {
        found.push(element.shadowRoot);
        allRoots(element.shadowRoot, found);
      }
    }
    return found;
  }

  function visible(element) {
    if (element.type === "file") return true; // file inputs are often hidden behind a styled button
    const style = getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const rect = element.getBoundingClientRect();
    return rect.width > 0 || rect.height > 0 || element.getClientRects().length > 0;
  }

  function textById(root, ids) {
    return ids
      .split(/\s+/)
      .map((id) => root.getElementById?.(id) || document.getElementById(id))
      .filter(Boolean)
      .map((el) => clean(el.innerText || el.textContent))
      .join(" ");
  }

  function labelFor(element) {
    const root = element.getRootNode();
    if (element.getAttribute("aria-labelledby")) {
      const text = textById(root, element.getAttribute("aria-labelledby"));
      if (text) return text;
    }
    if (element.getAttribute("aria-label")) return clean(element.getAttribute("aria-label"));
    if (element.id) {
      const label = root.querySelector?.(`label[for="${CSS.escape(element.id)}"]`);
      if (label) return clean(label.innerText || label.textContent);
    }
    const wrapping = element.closest("label");
    if (wrapping) return clean(wrapping.innerText || wrapping.textContent);
    // The nearest container with a short text before the field (question text above an input).
    let node = element.parentElement;
    for (let depth = 0; node && depth < 4; depth++, node = node.parentElement) {
      const text = clean(node.innerText || "");
      if (text && text.length < 400 && node.querySelectorAll("input, select, textarea").length <= 1) return text;
    }
    return clean(element.getAttribute("placeholder") || element.getAttribute("name") || "");
  }

  function groupLabel(radio) {
    const fieldset = radio.closest("fieldset");
    const legend = fieldset?.querySelector("legend");
    if (legend) return clean(legend.innerText || legend.textContent);
    const group = radio.closest("[role='radiogroup'], [role='group']");
    if (group?.getAttribute("aria-labelledby")) return textById(radio.getRootNode(), group.getAttribute("aria-labelledby"));
    if (group?.getAttribute("aria-label")) return clean(group.getAttribute("aria-label"));
    // The text above the group: the closest ancestor containing all radios of the group plus a question.
    const name = radio.name;
    let node = radio.parentElement;
    for (let depth = 0; node && depth < 6; depth++, node = node.parentElement) {
      const radios = node.querySelectorAll(`input[type='radio'][name="${CSS.escape(name)}"]`);
      const text = clean(node.innerText || "");
      if (radios.length > 1 && text.length < 600) {
        const options = [...radios].map(optionLabel);
        const question = options.reduce((t, o) => t.replace(o, ""), text).trim();
        if (question) return question;
      }
    }
    return clean(radio.name || "");
  }

  function optionLabel(input) {
    const root = input.getRootNode();
    if (input.id) {
      const label = root.querySelector?.(`label[for="${CSS.escape(input.id)}"]`);
      if (label) return clean(label.innerText || label.textContent);
    }
    const wrapping = input.closest("label");
    if (wrapping) return clean(wrapping.innerText || wrapping.textContent);
    const next = input.nextSibling;
    return clean(next?.textContent || input.value || "");
  }

  function fieldType(element) {
    if (element.tagName === "SELECT") return "select";
    if (element.tagName === "TEXTAREA") return "textarea";
    if (element.isContentEditable || element.getAttribute("role") === "textbox") return "contenteditable";
    const type = (element.type || "text").toLowerCase();
    if (["email", "tel", "url", "number", "date", "file", "checkbox", "radio"].includes(type)) return type;
    return "text";
  }

  /** The fields the user can see and fill now: the current step of a multi-step form. */
  function fieldElements() {
    const found = [];
    for (const root of allRoots()) {
      for (const element of root.querySelectorAll(SELECTOR)) {
        const type = (element.type || "").toLowerCase();
        if (SKIP_TYPES.has(type) || element.disabled || element.readOnly || !visible(element)) continue;
        if (CAPTCHA.test(`${element.name} ${element.id} ${element.className}`)) continue;
        found.push(element);
      }
    }
    return found;
  }

  function scan() {
    const fields = [];
    const radiosSeen = new Set();
    for (const element of fieldElements()) {
      const kind = fieldType(element);
      if (kind === "radio") {
        const key = `${element.form ? "f" : "d"}:${element.name}`;
        if (!element.name) element.setAttribute(ATTR, "rf-none"); // seen, so the step watcher doesn't wait on it
        if (!element.name || radiosSeen.has(key)) continue;
        radiosSeen.add(key);
        const group = [...(element.form || element.getRootNode()).querySelectorAll(`input[type='radio'][name="${CSS.escape(element.name)}"]`)];
        const id = group.map((radio) => radio.getAttribute(ATTR)).find(Boolean) || nextId();
        group.forEach((radio) => radio.setAttribute(ATTR, id));
        fields.push({
          field_id: id,
          label: groupLabel(element),
          type: "radio",
          options: group.map(optionLabel).filter(Boolean),
          required: group.some((r) => r.required || r.getAttribute("aria-required") === "true"),
          name: element.name,
          filled: hasValue(group),
        });
        continue;
      }
      const id = element.getAttribute(ATTR) || nextId();
      element.setAttribute(ATTR, id);
      const label = labelFor(element);
      fields.push({
        field_id: id,
        label,
        type: kind,
        options: kind === "select" ? [...element.options].map((o) => clean(o.text)).filter((t) => t && !/^(select|choose|please select|--)/i.test(t)) : [],
        required: Boolean(element.required || element.getAttribute("aria-required") === "true" || /\*\s*$/.test(label)),
        max_length: element.maxLength > 0 ? element.maxLength : null,
        name: element.name || element.id || null,
        placeholder: element.getAttribute("placeholder") || null,
        filled: hasValue([element]),
      });
    }
    return fields;
  }

  function find(id) {
    for (const root of allRoots()) {
      const found = root.querySelectorAll(`[${ATTR}="${CSS.escape(id)}"]`);
      if (found.length) return [...found];
    }
    return [];
  }

  /** Sets a value the way typing does, so React/Vue-controlled inputs keep it. */
  function setValue(element, value) {
    const proto = element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    element.focus?.();
    if (setter) setter.call(element, value);
    else element.value = value;
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    element.dispatchEvent(new Event("blur", { bubbles: true }));
  }

  function mark(elements, state, note) {
    const color = state === "filled" ? "#16a34a" : "#f59e0b";
    for (const element of elements) {
      const target = element.type === "radio" ? element.closest("fieldset, [role='radiogroup']") || element : element;
      target.style.outline = `2px solid ${color}`;
      target.style.outlineOffset = "2px";
      if (note) target.title = `ResumeFlow: ${note}`;
    }
  }

  function fillOne(elements, answer) {
    const element = elements[0];
    const value = String(answer.value);
    const type = fieldType(element);
    if (type === "file") return false;
    if (type === "radio") {
      const wanted = value.toLowerCase();
      const radio = elements.find((r) => optionLabel(r).toLowerCase() === wanted) || elements.find((r) => optionLabel(r).toLowerCase().startsWith(wanted));
      if (!radio || CONSENT.test(optionLabel(radio))) return false;
      if (!radio.checked) radio.click();
      return radio.checked;
    }
    if (type === "checkbox") {
      if (CONSENT.test(labelFor(element))) return false; // never tick a consent or certification box
      const yes = /^(yes|true|checked|on)$/i.test(value);
      if (element.checked !== yes) element.click();
      return element.checked === yes;
    }
    if (type === "select") {
      const option = [...element.options].find((o) => clean(o.text).toLowerCase() === value.toLowerCase()) || [...element.options].find((o) => o.value === value);
      if (!option) return false;
      element.value = option.value;
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }
    if (type === "contenteditable") {
      element.focus();
      element.textContent = value;
      element.dispatchEvent(new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" }));
      return true;
    }
    setValue(element, value);
    return element.value === value || element.value.length > 0;
  }

  /** The field already has an answer: typed by the user, restored by the site, or filled earlier. */
  function hasValue(elements) {
    const element = elements[0];
    const type = fieldType(element);
    if (type === "radio") return elements.some((radio) => radio.checked);
    if (type === "checkbox") return element.checked;
    if (type === "select") return element.selectedIndex > 0 && Boolean(element.value);
    if (type === "contenteditable") return Boolean(clean(element.textContent));
    if (type === "file") return element.files?.length > 0;
    return Boolean(element.value);
  }

  /** keepFilled (automatic answers for a new step): fields that already have a value are left as they are. */
  function fill(answers, { keepFilled = false } = {}) {
    const result = { filled: 0, needs: 0, failed: 0, kept: 0 };
    for (const answer of answers) {
      const elements = find(answer.field_id);
      if (!elements.length) continue;
      if (keepFilled && hasValue(elements)) {
        result.kept += 1;
        continue;
      }
      if (answer.value === null || answer.value === undefined || answer.source === "needs_input") {
        mark(elements, "needs", answer.note || "Needs your answer");
        result.needs += 1;
        continue;
      }
      let ok = false;
      try {
        ok = fillOne(elements, answer);
      } catch {
        ok = false;
      }
      mark(elements, ok ? "filled" : "needs", ok ? `Filled from ${answer.source.replace("_", " ")}` : "Couldn't fill this field; enter it yourself");
      result[ok ? "filled" : "failed"] += 1;
    }
    return result;
  }

  /** Puts the generated PDF into file inputs, where the site accepts a programmatic file (EXT-10). */
  function attach(items, { keepFilled = false } = {}) {
    const result = { attached: 0, failed: 0 };
    for (const item of items) {
      const [input] = find(item.field_id);
      if (!input || input.type !== "file" || (keepFilled && input.files?.length)) continue;
      try {
        const bytes = Uint8Array.from(atob(item.base64), (c) => c.charCodeAt(0));
        const transfer = new DataTransfer();
        transfer.items.add(new File([bytes], item.filename, { type: item.mime }));
        input.files = transfer.files;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
        const ok = input.files?.length === 1;
        mark([input], ok ? "filled" : "needs", ok ? `Attached ${item.filename}` : "Attach the file yourself");
        result[ok ? "attached" : "failed"] += 1;
      } catch {
        mark([input], "needs", "Attach the file yourself");
        result.failed += 1;
      }
    }
    return result;
  }

  let stepWatch = null;

  /** After the first answers on this page: reports when fields appear that haven't been read yet (the next step or tab
   * of a multi-step form, or a follow-up question), so the service worker answers them too. Stops after 30 minutes. */
  function watchSteps() {
    if (stepWatch) return;
    let timer = null;
    const check = () => {
      timer = null;
      if (!fieldElements().some((element) => !element.getAttribute(ATTR))) return;
      try {
        chrome.runtime.sendMessage({ type: "form-step", url: location.href })?.catch?.(() => {});
      } catch {
        // the extension was reloaded; this page's script is orphaned
      }
    };
    const observer = new MutationObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(check, 800);
    });
    // Steps appear as new elements, or as hidden ones shown (style, class, hidden, aria-hidden).
    observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["style", "class", "hidden", "aria-hidden"] });
    stepWatch = observer;
    setTimeout(() => {
      observer.disconnect();
      stepWatch = null;
    }, 30 * 60_000);
  }

  globalThis.__resumeflowForm = { scan, fill, attach, watchSteps, labelFor, CONSENT };
})();
