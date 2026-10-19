// Freezes the Python backend into backend/dist/resumeflow-backend/ and smoke-tests it
// with a full workflow: import DOCX + PDF resumes, verify, match a job, generate,
// approve and export — all against the frozen executable.
// Called by `npm run release` (via tauri.release.conf.json); can also be run directly:
//
//   node scripts/build-sidecar.mjs
//   node scripts/build-sidecar.mjs --smoke-only   (re-test an existing build without freezing again)
import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const backend = path.join(root, "backend");
const python = process.env.RESUMEFLOW_PYTHON ?? path.join(backend, ".venv", "Scripts", "python.exe");
const exe = path.join(backend, "dist", "resumeflow-backend", "resumeflow-backend.exe");

function run(command, args) {
  const result = spawnSync(command, args, { cwd: backend, stdio: "inherit" });
  if (result.status !== 0) {
    console.error(`[sidecar] ${path.basename(command)} ${args.join(" ")} failed`);
    process.exit(result.status ?? 1);
  }
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer().listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
    server.on("error", reject);
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function smokeTest() {
  const port = await freePort();
  const token = randomBytes(32).toString("base64url");
  const work = mkdtempSync(path.join(os.tmpdir(), "resumeflow-smoke-"));
  run(python, ["-m", "resumeflow.devtools.make_samples", path.join(work, "samples")]);
  const child = spawn(exe, ["--port", String(port)], {
    env: { ...process.env, RESUMEFLOW_SESSION_TOKEN: token, RESUMEFLOW_DATA_DIR: path.join(work, "data"), RESUMEFLOW_AUTO_BACKUP: "false", RESUMEFLOW_SECRET_BACKEND: "memory" },
    stdio: "ignore",
  });
  const base = `http://127.0.0.1:${port}`;
  const auth = { Authorization: `Bearer ${token}` };
  const call = async (method, route, body) => {
    const init = { method, headers: { ...auth } };
    if (body instanceof FormData) init.body = body;
    else if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const response = await fetch(base + route, init);
    const data = response.status === 204 ? null : await response.json();
    if (!response.ok) throw new Error(`${method} ${route} -> ${response.status}: ${JSON.stringify(data)}`);
    return data;
  };
  const upload = async (file) => {
    const form = new FormData();
    form.append("file", new Blob([readFileSync(file)]), path.basename(file));
    return call("POST", "/candidates/import", form);
  };
  const waitFor = async (route, predicate, label) => {
    const deadline = Date.now() + 60_000;
    let data;
    while (Date.now() < deadline) {
      data = await call("GET", route);
      if (predicate(data)) return data;
      await sleep(500);
    }
    const errors = [...(data?.resume?.issues ?? []), ...(data?.cover_letter?.issues ?? [])].filter((i) => i.severity === "error");
    const last = {
      status: data?.status,
      error: data?.error,
      processing: data?.processing?.map((p) => `${p.job_type}:${p.status}:${p.error ?? ""}`),
      errors: errors.map((i) => `${i.code}: ${i.message}`),
    };
    throw new Error(`timed out waiting for ${label}; last state ${JSON.stringify(last)}`);
  };

  try {
    const deadline = Date.now() + 30_000;
    for (;;) {
      try {
        const health = await fetch(`${base}/health`).then((r) => r.json());
        if (health.status === "ok") break;
      } catch {
        if (child.exitCode !== null) throw new Error(`frozen backend exited with code ${child.exitCode}`);
      }
      if (Date.now() > deadline) throw new Error("frozen backend did not become healthy within 30 s");
      await sleep(400);
    }

    await call("PATCH", "/settings", { output_root: path.join(work, "out"), pdf_converter: "none" });
    const resumes = path.join(work, "samples", "resumes");
    const docx = readdirSync(resumes).find((f) => f.startsWith("Avery") && f.endsWith(".docx"));
    const pdf = readdirSync(resumes).find((f) => f.endsWith(".pdf"));
    const imported = await upload(path.join(resumes, docx));
    await upload(path.join(resumes, pdf)); // exercises PyMuPDF in the frozen build
    const candidate = imported.candidate;

    const profile = candidate.profile;
    profile.summary_verified = true;
    profile.work_authorization_verified = Boolean(profile.work_authorization);
    for (const role of profile.employment) {
      role.verified = true;
      for (const bullet of role.bullets) bullet.verified = true;
    }
    await call("PATCH", `/candidates/${candidate.id}`, { profile, consent_confirmed: true });
    await call("POST", `/candidates/${candidate.id}/skills/verify-with-evidence`);
    await call("POST", `/candidates/${candidate.id}/review-complete`);

    const jobFile = readdirSync(path.join(work, "samples", "jobs")).find((f) => f.startsWith("Acme"));
    const [urlLine, , ...rest] = readFileSync(path.join(work, "samples", "jobs", jobFile), "utf-8").split("\n");
    const job = await call("POST", "/jobs", { job_url: urlLine.replace("URL: ", "").trim(), raw_jd: rest.join("\n") });
    const top = job.matches.find((m) => m.rank === 1);
    if (!top) throw new Error("matching produced no eligible candidate");

    // Default setting: Generate saves the files without a manual approval step.
    const app = await call("POST", "/applications", { job_id: job.id, candidate_id: top.candidate_id, instructions: "Keep bullets concise." });
    const exported = await waitFor(`/applications/${app.id}`, (a) => a.status === "EXPORTED" || a.status === "FAILED", "generation and automatic export");
    if (exported.status !== "EXPORTED" || exported.approved_by !== "automatic") throw new Error(`automatic export failed: ${exported.status} ${exported.error ?? ""}`);
    const files = exported.files.filter((f) => f.exists).map((f) => path.basename(f.path));
    const resumeFile = exported.files.find((f) => f.type === "resume" && f.format === "docx");
    if (files.length < 5 || !resumeFile || statSync(resumeFile.path).size < 5_000) throw new Error(`unexpected export: ${files.join(", ")}`);

    // Batch path: save a job for later, then one background run analyzes, matches and generates.
    const batch = await call("POST", "/jobs/batch", {
      items: [{ job_url: "https://careers.example.com/jobs/smoke-batch", raw_jd: rest.join("\n") }],
      confirm_duplicates: true,
      process: true,
    });
    const savedId = batch.results[0]?.job?.id;
    if (!savedId || batch.queued[0] !== savedId) throw new Error(`batch save failed: ${JSON.stringify(batch)}`);
    // v1.8: the job waits for answers about job skills the profile doesn't show; answering (one skill used at a role,
    // the rest unsure) and approving the drafted bullet lets it continue to generation and export by itself.
    const waiting = await waitFor(`/jobs/${savedId}`, (j) => j.needs_confirmation || j.applications.length > 0, "batch analysis and matching");
    let answered = 0;
    if (waiting.needs_confirmation) {
      const gaps = await call("POST", "/jobs/skill-gaps/batch", {});
      const bucket = gaps.candidates.find((c) => c.job_ids.includes(savedId));
      if (!bucket || !bucket.skills.length) throw new Error(`no skill gaps reported for the waiting job: ${JSON.stringify(gaps)}`);
      const [first, ...others] = bucket.skills;
      const answers = [
        { skill: first.skill, decision: "used", role_ids: [bucket.roles[0].id], context: `built data pipelines with ${first.skill}` },
        ...others.map((s) => ({ skill: s.skill, decision: "unsure" })),
      ];
      const result = await call("POST", `/candidates/${bucket.candidate_id}/skills/confirm-bulk`, { answers, source_job_ids: bucket.job_ids });
      if (!result.suggestions.length) throw new Error("no bullet was drafted from the confirmed work");
      await call("POST", `/candidates/${bucket.candidate_id}/experience-evidence/approve`, { ids: result.suggestions.map((s) => s.id), acknowledge: true });
      answered = answers.length;
    }
    await waitFor(`/jobs/${savedId}`, (j) => j.applications.some((a) => a.status === "EXPORTED"), "batch processing and automatic export");
    // Default (no approval step): describing work with a missing job skill adds the bullet at once.
    let instant = 0;
    const report = await call("GET", `/jobs/${savedId}/skill-gaps`);
    const open = report.items.find((i) => i.needs_answer);
    if (open && report.roles.length) {
      const result = await call("POST", `/candidates/${report.candidate_id}/skills/confirm-bulk`, {
        answers: [{ skill: open.skill, decision: "used", role_ids: [report.roles[0].id], context: `automated reporting with ${open.skill}` }],
        source_job_ids: [savedId],
      });
      instant = result.approved?.length ?? 0;
      if (!instant && !result.suggestions.length) throw new Error(`no bullet was written from the description: ${JSON.stringify(result)}`);
    }

    // Templates: copy a stored resume's style (python-docx + lxml in the frozen build) and render samples with it.
    const versionId = candidate.resume_versions[0].id;
    const template = await call("POST", "/templates/from-resume", { candidate_id: candidate.id, version_id: versionId });
    await call("PATCH", "/settings", { resume_template: template.key });
    const samples = await call("POST", "/templates/sample", { key: template.key });
    if (statSync(samples.resume_docx).size < 5_000) throw new Error("template sample was not written");
    console.log(
      `[sidecar] smoke test passed: imported DOCX+PDF, matched, generated and auto-exported ${files.length} files; batch job processed (${answered ? `${answered} skill answers, bullet approved` : "generated without waiting"}; ${instant} bullet added at once); template "${template.name}" copied (${template.style.font} ${template.style.size}pt)`,
    );
  } finally {
    child.kill();
    await sleep(800);
    rmSync(work, { recursive: true, force: true });
  }
}

if (!process.argv.includes("--smoke-only")) run(python, ["-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN", path.join("packaging", "resumeflow-backend.spec")]);
if (!existsSync(exe)) {
  console.error(`[sidecar] expected ${exe} after PyInstaller`);
  process.exit(1);
}
try {
  await smokeTest();
} catch (error) {
  console.error(`[sidecar] smoke test failed: ${error instanceof Error ? error.message : error}`);
  process.exit(1);
}
