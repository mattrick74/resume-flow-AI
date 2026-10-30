// Shared by the service worker and the injected capture script. Keep this file as a classic-script-compatible IIFE:
// chrome.scripting can inject it into page frames, while the module service worker imports it for the same decisions.
(() => {
  const GENERIC_SOURCES = new Set(["", "page text", "main text", "body text"]);
  const JOB_WORDS =
    /\b(?:applicants?|apply|benefits?|candidates?|compensation|degree|duties|education|employment|experience|hiring|job|position|qualifications?|requirements?|responsibilities|role|salary|skills?|work)\b/gi;
  const WIDGET_TITLE =
    /\b(?:twitter|facebook|instagram|tiktok|youtube)\s+(?:widget|iframe|embed)\b|\b(?:social\s+widget|advertisement|tracking\s+pixel|captcha)\b/i;

  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();

  /** Strong code-shape detection: ordinary prose saying "JavaScript" or "function" is deliberately not enough. */
  function looksLikeJavaScript(value) {
    const text = String(value || "").trim();
    if (text.length < 160) return false;
    const sample = text.slice(0, 24_000);
    const markerPattern =
      /!function\s*\(|\bfunction\s*\(|=>|\b(?:var|let|const)\s+[$A-Z_a-z]|Function\.prototype|Object\.defineProperty|Symbol\.toStringTag|__esModule|__webpack|webpackJsonp|\brequire\s*\(|\breturn(?:\s+|\()/g;
    const markers = sample.match(markerPattern) || [];
    const strongMarkers = sample.match(/!function\s*\(|Function\.prototype|Object\.defineProperty|Symbol\.toStringTag|__esModule|__webpack|webpackJsonp/g) || [];
    const syntax = (sample.match(/[{};()[\]=><]/g) || []).length / sample.length;
    const words = (sample.match(/\b[A-Za-z]{2,}\b/g) || []).length;
    const markerRatio = markers.length / Math.max(words, 1);
    const longestLine = Math.max(...sample.split(/\r?\n/).map((line) => line.length));
    return (
      (strongMarkers.length >= 2 && markers.length >= 3 && syntax >= 0.04) ||
      (markers.length >= 6 && syntax >= 0.055 && (markerRatio >= 0.01 || longestLine >= 700))
    );
  }

  function jobEvidence(value) {
    return new Set((String(value || "").match(JOB_WORDS) || []).map((word) => word.toLowerCase())).size;
  }

  function sourceStrength(capture) {
    const source = clean(capture?.read_by).toLowerCase();
    if (source === "selection" || source === "structured data") return 5;
    if (source === "job description element") return 4;
    if (!GENERIC_SOURCES.has(source)) return 4; // a supported site-specific reader
    return 1;
  }

  function looksLikeWidget(capture) {
    const identity = `${capture?.title || ""} ${capture?.page_title || ""}`;
    if (WIDGET_TITLE.test(identity)) return true;
    const site = clean(capture?.site).toLowerCase();
    return /^(?:platform|syndication)\.twitter\.com$|^(?:www\.)?(?:facebook|instagram|tiktok)\.com$/.test(site);
  }

  function plausibleCapture(capture) {
    const description = String(capture?.description || "").trim();
    if (description.length < 160 || looksLikeJavaScript(description) || looksLikeWidget(capture)) return false;
    return sourceStrength(capture) >= 4 || (jobEvidence(description) >= 2 && (description.match(/\b[A-Za-z]{3,}\b/g) || []).length >= 35);
  }

  function score(capture) {
    const description = String(capture?.description || "");
    return sourceStrength(capture) * 100 + jobEvidence(description) * 12 + Math.min(description.length, 12_000) / 120;
  }

  function meaningfulTitle(value) {
    const title = clean(value);
    const generic =
      /^(?:home|apply|job search|open positions?|current openings?|career opportunities|job opportunities|join our team|(?:.+\s+)?careers?|jobs?(?:\s+(?:at|with)\s+.+)?)$/i;
    return title && !WIDGET_TITLE.test(title) && !generic.test(title) ? title : "";
  }

  function withoutCode(capture) {
    if (!capture) return null;
    const description = String(capture.description || "").trim();
    return looksLikeJavaScript(description) ? { ...capture, description: "" } : { ...capture, description };
  }

  function withTopPage(candidate, top) {
    if (!top || candidate === top) return candidate;
    return {
      ...candidate,
      url: top.url || candidate.url,
      page_title: top.page_title || candidate.page_title,
      title: meaningfulTitle(top.title) || candidate.title,
      company: top.company || candidate.company,
      location: top.location || candidate.location,
    };
  }

  /** Keep the top page by default; an embedded frame wins only when it looks like an actual, better job posting. */
  function pickCapture(results) {
    const records = (results || []).filter((record) => record?.result);
    if (!records.length) return null;
    const topRecord = records.find((record) => record.frameId === 0) || records[0];
    // A deliberate 200+ character selection is user-authoritative, including a technical description with code samples.
    const selected = records.find((record) => record.result?.read_by === "selection" && String(record.result.description || "").trim());
    if (selected) return withTopPage(selected.result, topRecord.result);
    const top = withoutCode(topRecord.result);

    const frames = records
      .filter((record) => record !== topRecord)
      .map((record) => withoutCode(record.result))
      .filter(plausibleCapture)
      .sort((left, right) => score(right) - score(left));
    const best = frames[0];
    if (!best) return top;

    const topCredible = plausibleCapture(top);
    const topLength = String(top?.description || "").length;
    const bestLength = String(best.description || "").length;
    const strongerSource = sourceStrength(best) >= 4;
    const clearlyBetterGeneric = bestLength > Math.max(600, topLength * 2.5) && jobEvidence(best.description) >= 4;
    const clearlyBetterStrong = strongerSource && bestLength > Math.max(300, topLength * 1.15);
    return !topCredible || clearlyBetterStrong || clearlyBetterGeneric ? withTopPage(best, top) : top;
  }

  globalThis.__resumeflowCaptureQuality = { looksLikeJavaScript, plausibleCapture, pickCapture };
})();
