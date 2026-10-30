// Reads the job posting on the current page (requirements EXT-03): site readers for common job sites, then the
// posting's structured data (schema.org JobPosting, published by most applicant tracking systems), then the page's
// main text. A text selection of 200+ characters is used as the description as-is.
(() => {
  const quality = globalThis.__resumeflowCaptureQuality;
  const EXCLUDED_TEXT = "script, style, noscript, template, svg, canvas, iframe";
  const SITES = [
    {
      site: "linkedin",
      host: /(^|\.)linkedin\.com$/,
      title: [".job-details-jobs-unified-top-card__job-title h1", ".job-details-jobs-unified-top-card__job-title", ".jobs-unified-top-card__job-title", ".top-card-layout__title", "h1"],
      company: [".job-details-jobs-unified-top-card__company-name", ".jobs-unified-top-card__company-name", ".topcard__org-name-link", ".top-card-layout__second-subline a"],
      location: [".job-details-jobs-unified-top-card__primary-description-container .tvm__text", ".jobs-unified-top-card__bullet", ".topcard__flavor--bullet"],
      description: ["#job-details", ".jobs-description__content", ".jobs-box__html-content", ".show-more-less-html__markup", ".description__text"],
    },
    {
      site: "indeed",
      host: /(^|\.)indeed\.[a-z.]+$/,
      title: ["[data-testid='jobsearch-JobInfoHeader-title']", "h1.jobsearch-JobInfoHeader-title", ".jobsearch-JobInfoHeader-title"],
      company: ["[data-testid='inlineHeader-companyName']", "[data-company-name='true']"],
      location: ["[data-testid='inlineHeader-companyLocation']", "[data-testid='job-location']"],
      description: ["#jobDescriptionText"],
    },
    {
      site: "greenhouse",
      host: /(^|\.)greenhouse\.io$/,
      title: [".job__title h1", ".app-title", "h1.section-header", "h1"],
      company: [".job__company", ".company-name"],
      location: [".job__location", ".location"],
      description: [".job__description", "#content", "#app_body .content"],
    },
    {
      site: "lever",
      host: /(^|\.)lever\.co$/,
      title: [".posting-headline h2"],
      company: [".main-header-logo img[alt]"],
      location: [".posting-categories .location", ".posting-category.location"],
      description: ["[data-qa='job-description']", ".section-wrapper.page-full-width", ".content"],
    },
    {
      site: "workday",
      host: /(^|\.)myworkdayjobs\.com$|(^|\.)workday\.com$/,
      title: ["[data-automation-id='jobPostingHeader']"],
      company: [],
      location: ["[data-automation-id='locations'] dd", "[data-automation-id='locations']"],
      description: ["[data-automation-id='jobPostingDescription']"],
    },
    {
      site: "ashby",
      host: /(^|\.)ashbyhq\.com$/,
      title: ["h1"],
      company: [],
      location: ["[class*='_location']"],
      description: ["[class*='_descriptionText']", "#overview", "[class*='_description_']"],
    },
    {
      site: "smartrecruiters",
      host: /(^|\.)smartrecruiters\.com$/,
      title: ["h1.job-title", "h1[itemprop='title']", "h1"],
      company: ["[itemprop='hiringOrganization'] [itemprop='name']"],
      location: ["[itemprop='jobLocation']", ".job-details li"],
      description: [".job-sections", "[itemprop='description']"],
    },
    {
      site: "icims",
      host: /(^|\.)icims\.com$/,
      title: [".iCIMS_Header", "h1"],
      company: [],
      location: [".iCIMS_JobHeaderTag", ".header .left"],
      description: [".iCIMS_JobContent", ".iCIMS_InfoMsg_Job"],
    },
    {
      site: "dice",
      host: /(^|\.)dice\.com$/,
      title: ["h1[data-cy='jobTitle']", "h1"],
      company: ["[data-cy='companyNameLink']"],
      location: ["[data-cy='location']"],
      description: ["[data-testid='jobDescriptionHtml']", "#jobDescription", ".job-description"],
    },
    {
      site: "ziprecruiter",
      host: /(^|\.)ziprecruiter\.com$/,
      title: ["h1.job_title", "h1"],
      company: [".hiring_company_text", ".hiring_company a"],
      location: [".hiring_location"],
      description: [".job_description", ".jobDescriptionSection"],
    },
  ];

  const clean = (value) => (value || "").replace(/ /g, " ").replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim();

  function textOf(element) {
    if (!element) return "";
    if (element.tagName === "IMG") return clean(element.getAttribute("alt"));
    // innerText already excludes non-rendered script/style contents. If the browser supplies it, an empty string is a
    // real result and must not fall through to textContent (the source of 60k minified-widget captures).
    if (typeof element.innerText === "string") return clean(element.innerText);
    const readable = element.cloneNode(true);
    readable.querySelectorAll?.(EXCLUDED_TEXT).forEach((node) => node.remove());
    return clean(readable.textContent || "");
  }

  function first(selectors, root = document) {
    for (const selector of selectors || []) {
      try {
        for (const element of root.querySelectorAll(selector)) {
          const text = textOf(element);
          if (text) return text;
        }
      } catch {
        // an unsupported selector on this page
      }
    }
    return "";
  }

  /** HTML (as in JSON-LD descriptions) to readable text with line breaks and list markers. */
  function htmlToText(html) {
    const marked = String(html || "")
      .replace(/<\s*br\s*\/?>/gi, "\n")
      .replace(/<\s*li[^>]*>/gi, "\n- ")
      .replace(/<\/\s*(p|div|h[1-6]|ul|ol|section|tr|table|header|footer)\s*>/gi, "\n"); // a list item ends where the next one starts
    const doc = new DOMParser().parseFromString(`<body>${marked}</body>`, "text/html");
    doc.body.querySelectorAll(EXCLUDED_TEXT).forEach((node) => node.remove());
    return clean(doc.body.textContent || "")
      .split("\n")
      .map((line) => line.trim())
      .join("\n")
      .replace(/\n{3,}/g, "\n\n");
  }

  function jobPostings(value) {
    if (!value || typeof value !== "object") return [];
    if (Array.isArray(value)) return value.flatMap(jobPostings);
    const type = value["@type"];
    const isJob = type === "JobPosting" || (Array.isArray(type) && type.includes("JobPosting"));
    return [...(isJob ? [value] : []), ...jobPostings(value["@graph"])];
  }

  function readJsonLd(root = document) {
    for (const script of root.querySelectorAll('script[type="application/ld+json"]')) {
      let data;
      try {
        data = JSON.parse(script.textContent || "");
      } catch {
        continue;
      }
      const job = jobPostings(data)[0];
      if (!job) continue;
      const org = Array.isArray(job.hiringOrganization) ? job.hiringOrganization[0] : job.hiringOrganization;
      const place = Array.isArray(job.jobLocation) ? job.jobLocation[0] : job.jobLocation;
      const address = place?.address || {};
      const location = [address.addressLocality, address.addressRegion].filter(Boolean).join(", ") || (job.jobLocationType === "TELECOMMUTE" ? "Remote" : "");
      return {
        title: clean(job.title),
        company: clean(typeof org === "string" ? org : org?.name),
        location: clean(location),
        description: htmlToText(job.description),
        employment_type: clean(Array.isArray(job.employmentType) ? job.employmentType.join(", ") : job.employmentType),
      };
    }
    return null;
  }

  function meta(name) {
    return clean(document.querySelector(`meta[property="${name}"], meta[name="${name}"]`)?.getAttribute("content"));
  }

  function mainText() {
    const candidates = [
      ["[class*='job-description']", "job description element"],
      ["[id*='job-description']", "job description element"],
      ["[class*='jobDescription']", "job description element"],
      ["[id*='jobDescription']", "job description element"],
      ["article", "page text"],
      ["main", "page text"],
      ["[role='main']", "page text"],
    ];
    let best = { text: "", read_by: "page text" };
    for (const [selector, readBy] of candidates) {
      for (const element of document.querySelectorAll(selector)) {
        const text = textOf(element);
        if (text.length > best.text.length && text.length < 60_000 && !quality?.looksLikeJavaScript(text)) best = { text, read_by: readBy };
      }
      if (best.text.length >= 400) return best;
    }
    const body = textOf(document.body).slice(0, 60_000);
    if (best.text || quality?.looksLikeJavaScript(body)) return best;
    return { text: body, read_by: "page text" };
  }

  /** The posting's own address: LinkedIn and Indeed search pages show the job in a pane with its id in the query. */
  function canonicalUrl(href = location.href) {
    let url;
    try {
      url = new URL(href);
    } catch {
      return href;
    }
    if (/(^|\.)linkedin\.com$/.test(url.hostname)) {
      const id = url.searchParams.get("currentJobId") || (url.pathname.match(/\/jobs\/view\/(\d+)/) || [])[1];
      if (id) return `https://www.linkedin.com/jobs/view/${id}/`;
    }
    if (/(^|\.)indeed\./.test(url.hostname)) {
      const id = url.searchParams.get("vjk") || url.searchParams.get("jk");
      if (id) return `${url.origin}/viewjob?jk=${id}`;
    }
    for (const key of [...url.searchParams.keys()]) {
      if (/^utm_|^(gclid|fbclid|trk|trackingId|refId|src|ref)$/i.test(key)) url.searchParams.delete(key);
    }
    url.hash = "";
    return url.toString();
  }

  function capture() {
    const selection = clean(String(window.getSelection?.() || ""));
    const site = SITES.find((s) => s.host.test(location.hostname));
    const ld = readJsonLd();
    const fromSite = site
      ? { title: first(site.title), company: first(site.company), location: first(site.location), description: first(site.description) }
      : {};
    const choices = [
      selection.length >= 200 ? { text: selection, read_by: "selection" } : null,
      fromSite.description ? { text: fromSite.description, read_by: site.site } : null,
      ld?.description ? { text: ld.description, read_by: "structured data" } : null,
    ];
    const chosen =
      choices.find((candidate) => candidate?.text && (candidate.read_by === "selection" || !quality?.looksLikeJavaScript(candidate.text))) || mainText();
    const description = chosen.text || "";
    const title = fromSite.title || ld?.title || meta("og:title") || first(["h1"]) || clean(document.title);
    return {
      url: canonicalUrl(),
      title: title.slice(0, 200),
      company: (fromSite.company || ld?.company || meta("og:site_name") || "").slice(0, 200),
      location: (fromSite.location || ld?.location || "").slice(0, 200),
      employment_type: (ld?.employment_type || "").slice(0, 100),
      description: description.slice(0, 100_000),
      site: location.hostname.replace(/^www\./, ""),
      page_title: clean(document.title).slice(0, 300),
      read_by: chosen.read_by,
    };
  }

  globalThis.__resumeflowCapture = { capture, readJsonLd, htmlToText, canonicalUrl, SITES };
})();
