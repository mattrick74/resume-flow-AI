// Page identity shared by the extension service worker and side panel.
// Job boards such as LinkedIn and Indeed replace the selected posting inside
// the same Chrome tab. A tab id therefore is not enough to identify the job.

const APPLY_PATH = /\/(?:apply|application|applications)(?:\/|$)/i;

function parsed(rawUrl) {
  try {
    return new URL(rawUrl || "");
  } catch {
    return null;
  }
}

function hostMatches(hostname, domain) {
  return hostname === domain || hostname.endsWith(`.${domain}`);
}

function pathKey(site, url) {
  if (APPLY_PATH.test(url.pathname)) return null;
  const path = url.pathname.replace(/\/+$/, "");
  return path && path !== "/" ? `${site}:${path.toLowerCase()}` : null;
}

/** A stable identity only when the address clearly names a job posting. */
export function jobPostingKey(rawUrl) {
  const url = parsed(rawUrl);
  if (!url) return null;
  const hostname = url.hostname.toLowerCase().replace(/^www\./, "");

  if (hostMatches(hostname, "linkedin.com")) {
    const id = url.searchParams.get("currentJobId") || (url.pathname.match(/\/jobs\/view\/(\d+)/i) || [])[1];
    return id ? `linkedin:${id}` : null;
  }

  if (/^indeed\.[a-z.]+$/.test(hostname) || /\.indeed\.[a-z.]+$/.test(hostname)) {
    const id = url.searchParams.get("vjk") || url.searchParams.get("jk");
    return id ? `indeed:${id}` : null;
  }

  if (hostMatches(hostname, "greenhouse.io")) {
    const id = (url.pathname.match(/\/jobs\/(\d+)/i) || [])[1];
    return id && !APPLY_PATH.test(url.pathname) ? `greenhouse:${id}` : null;
  }

  if (hostMatches(hostname, "lever.co")) return pathKey("lever", url);
  if (hostMatches(hostname, "myworkdayjobs.com") || hostMatches(hostname, "workday.com")) {
    return /\/job\//i.test(url.pathname) ? pathKey("workday", url) : null;
  }
  if (hostMatches(hostname, "ashbyhq.com")) return pathKey("ashby", url);
  if (hostMatches(hostname, "smartrecruiters.com")) return pathKey("smartrecruiters", url);
  if (hostMatches(hostname, "dice.com")) return /\/job-detail\//i.test(url.pathname) ? pathKey("dice", url) : null;
  if (hostMatches(hostname, "ziprecruiter.com")) return /\/jobs?\//i.test(url.pathname) ? pathKey("ziprecruiter", url) : null;
  return null;
}

/** Non-job/application pages deliberately keep the current link. */
export function scopeLinkedState(linked, rawUrl) {
  const postingKey = jobPostingKey(rawUrl);
  if (!postingKey || !linked || linked.postingKey === postingKey) return { state: linked, changed: false };
  // Drop every application-only flag together with the stale ids. Keeping any
  // of them could arm form filling or confirmation capture for the wrong job.
  return { state: { postingKey }, changed: true };
}

/** Old snapshots may be restored on ATS pages, but never onto another job. */
export function snapshotMatchesPosting(snapshotPostingKey, rawUrl) {
  const postingKey = jobPostingKey(rawUrl);
  return !postingKey || snapshotPostingKey === postingKey;
}
