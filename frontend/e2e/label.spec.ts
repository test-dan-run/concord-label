import * as fs from "node:fs";
import { expect, test, type Page } from "@playwright/test";

// Concord Label on the replayed fixture set: tests/e2e_server.py registers the fixture models, uploads the
// annotated fixture set ("Synth fixtures") and an audio-only copy of its audio ("Unlabelled calls"), and
// pseudo-labels the copy with pyannote -> both Whispers. Console errors fail the test.

function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error" && !m.text().includes("favicon")) errors.push(m.text());
  });
  return errors;
}

test("review console lists datasets with progress and a next action", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/label");
  await expect(page.getByRole("heading", { name: "Review console" })).toBeVisible();
  const row = page.getByRole("row", { name: /Unlabelled calls/ });
  await expect(row).toContainText("pseudo-labels");
  await expect(row.getByRole("img", { name: /Confirmed \d+%/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /Synth fixtures/ })).toContainText("uploaded references");
  await page.getByRole("button", { name: "Not labelled" }).click();
  await expect(page.getByRole("row", { name: /Unlabelled calls/ })).toHaveCount(0);
  await page.getByRole("button", { name: "All", exact: true }).click();
  await page.getByLabel("Search datasets by name or #tag").fill("#call");
  await expect(page.getByRole("row", { name: /Unlabelled calls/ })).toBeVisible();
  expect(errors).toEqual([]);
});

test("pseudo-label: saved outputs re-fuse, results show triage and agreement", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/label/pseudo");
  await page.getByRole("main").getByRole("link", { name: /Unlabelled calls/ }).click();
  await expect(page.getByRole("heading", { name: "Ensemble pseudo-labelling" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Re-fuse 2 saved outputs" })).toBeEnabled();
  await expect(page.getByRole("heading", { name: "Triage" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Member agreement" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Chosen as starting hypothesis" })).toBeVisible();
  // re-fuse with ROVER: no inference; a new run replaces the results (one word-timed member, so it
  // falls back to best-aligned)
  const before = await page.getByText(/ from run \d+/).textContent();
  await page.getByLabel("ROVER fusion").check();
  await page.getByRole("button", { name: "Re-fuse 2 saved outputs" }).click();
  await expect(page.getByText(/ from run \d+/)).not.toHaveText(before!, { timeout: 30_000 });
  await expect(page.getByText(/Fused with best-aligned/)).toBeVisible();
  // the form starts from the last run's settings, so the segmenter did not change
  await expect(page.getByText(/segments from pyannote-diarization/)).toBeVisible();
  expect(errors).toEqual([]);
});

test("annotate: edit, shortcuts, multi-select, bulk confirm, audio", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/label");
  await page.getByRole("row", { name: /Unlabelled calls/ }).getByRole("link", { name: /Review|Confirm|Check/ }).click();
  await expect(page).toHaveURL(/\/label\/annotate\/\d+/);
  await expect(page.getByRole("heading", { name: /Segments · \d+/ })).toBeVisible();
  await expect(page.getByRole("img", { name: "Speaker and error lanes" })).toBeVisible();
  await page.getByRole("button", { name: "All", exact: true }).click();
  // inline edit with autosave
  const first = page.getByLabel(/^Transcript at /).first();
  await first.fill("Edited in the browser.");
  await first.press("Enter");
  await expect(page.getByLabel(/^Transcript at /).first()).toHaveValue("Edited in the browser.");
  await page.reload();
  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(page.getByLabel(/^Transcript at /).first()).toHaveValue("Edited in the browser.");
  // Space plays the current segment
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("Space");
  await expect.poll(async () => page.locator("audio").first().evaluate((a: HTMLAudioElement) => a.currentTime), { timeout: 15_000 }).toBeGreaterThan(0.05);
  await page.keyboard.press("Space");
  // multi-select with shift-click, then the bulk bar
  const boxes = page.getByLabel(/^Select segment at /);
  await boxes.nth(0).click();
  await boxes.nth(2).click({ modifiers: ["Shift"] });
  const bar = page.getByRole("toolbar", { name: "Bulk actions" });
  await expect(bar).toContainText("3 selected");
  await bar.getByRole("button", { name: "Confirm" }).click();
  await expect(bar).toHaveCount(0);
  await expect(page.locator("tbody tr").nth(2)).toContainText("Confirmed");
  // 1–8 assigns a speaker to the current segment
  const pill = page.locator("tbody tr").nth(3).getByTitle(/cycle speakers/);
  await page.locator("tbody tr").nth(3).click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("2");
  await expect.poll(() => pill.textContent()).not.toBe("");
  const second = await pill.textContent();
  await page.keyboard.press("1");
  await expect(pill).not.toHaveText(second!);
  expect(errors).toEqual([]);
});

test("live evaluation loads standings and DER; Send to Bench, no locked score without Bench", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/label/live");
  await page.getByRole("main").getByRole("link", { name: /Unlabelled calls/ }).click();
  await expect(page.getByRole("heading", { name: "Live evaluation" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "WER vs confirmed segments" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Standings" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Diarisation (DER)" })).toBeVisible();
  // Send to Bench replaces Promote; without BENCH_URL the package is a download and the locked score is hidden
  await expect(page.getByRole("heading", { name: "Promote to benchmark dataset" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Send to Bench" })).toBeVisible();
  await expect(page.getByText("Download (zip)")).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Locked" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Use for retraining" })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("import / export: build a package, import it back, see the merge plan", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/label/sync");
  await expect(page.getByRole("heading", { name: "Import / export progress" })).toBeVisible();
  await page.getByLabel("Dataset").selectOption({ label: "Unlabelled calls" });
  await page.getByRole("button", { name: "All files" }).click();
  await page.getByRole("button", { name: "Build package" }).click();
  const link = page.getByRole("link", { name: /^Download .*\.zip/ });
  await expect(link).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
  const file = await download.path();
  await page.getByLabel("Label package (.zip)").setInputFiles(file!);
  await expect(page.getByLabel("Merge into")).toHaveValue(/\d+/);
  await expect(page.getByText(/files · Matched by fingerprint/)).toBeVisible();
  await expect(page.getByRole("cell", { name: "Unchanged" })).toBeVisible();
  await page.getByRole("button", { name: "Apply merge" }).click();
  await expect(page.getByText(/Merged into dataset/)).toBeVisible();
  await expect(page.getByRole("row", { name: /Import.*applied/ }).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("send to Bench without Bench: download a reference package, listed in the history", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/datasets");
  await page.getByRole("link", { name: "Synth fixtures" }).click();
  const panel = page.locator("section").filter({ has: page.getByRole("heading", { name: "Send to Bench" }) });
  await expect(panel.getByLabel("Package name")).toHaveValue("Synth fixtures");
  await expect(panel.getByText("Download (zip)")).toBeVisible();
  await expect(panel.getByText("speaker + text (SegLST)")).toBeVisible();
  await panel.getByLabel("Package name").fill("Synth refs");
  await panel.getByRole("button", { name: "Send to Bench" }).click();
  const link = panel.getByRole("link", { name: /^Download synth-refs-refs-v\d+\.zip/ });
  await expect(link).toBeVisible({ timeout: 30_000 });
  const [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
  expect(download.suggestedFilename()).toMatch(/^synth-refs-refs-v\d+\.zip$/);
  const file = await download.path();
  expect(fs.statSync(file!).size).toBeGreaterThan(1000);
  expect(fs.readFileSync(file!).subarray(0, 2).toString()).toBe("PK");
  const history = page.locator("section").filter({ has: page.getByRole("heading", { name: "Sent to Bench" }) });
  await expect(history.getByRole("row", { name: /v\d+ complete .*download done/ }).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("open in Label by sha256: the annotator opens on the file at t", async ({ page, request }) => {
  const errors = watchErrors(page);
  const datasets = (await (await request.get("/api/datasets")).json()) as { id: number; uid: string; name: string }[];
  const ds = datasets.find((d) => d.name === "Synth fixtures")!;
  const files = (await (await request.get(`/api/datasets/${ds.id}/files`)).json()) as { id: number; sha256: string; status: string; stem: string }[];
  const f = files.find((x) => x.status === "ok")!;
  const view = (await (await request.get(`/api/label/files/${f.id}`)).json()) as { segments: { start: number; end: number; text: string | null }[] };
  const seg = view.segments[1];
  const t = Math.round(((seg.start + seg.end) / 2) * 100) / 100;
  await page.goto(`/label/file/by-sha/${f.sha256}?dataset_uid=${ds.uid}&t=${t}`);
  await expect(page).toHaveURL(new RegExp(`/label/annotate/${ds.id}\\?file=${f.id}&t=`));
  await expect(page.getByRole("heading", { name: f.stem, exact: true })).toBeVisible();
  await expect(page.locator('tbody tr[aria-current="true"]').getByLabel(/^Transcript at /)).toHaveValue(seg.text ?? "");
  // an unknown fingerprint says so
  await page.goto(`/label/file/by-sha/${"0".repeat(64)}?dataset_uid=${ds.uid}`);
  await expect(page.getByText(/This audio is not in Concord Label/)).toBeVisible();
  expect(errors.filter((e) => !e.includes("404"))).toEqual([]);
});
