import * as fs from "node:fs";
import { expect, test, type Page } from "@playwright/test";

// Concord Label on the replayed fixture set: tests/e2e_server.py lists the fixture models from the replay catalog, uploads the
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
  await expect(page.locator("[data-lane]").first()).toBeVisible();
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

test("annotate: speaker lanes move, trim, draw and add a speaker", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/label");
  await page.getByRole("row", { name: /Unlabelled calls/ }).getByRole("link", { name: /Review|Confirm|Check/ }).click();
  await page.getByRole("combobox", { name: "File", exact: true }).selectOption({ index: 1 });
  await expect(page.getByRole("heading", { name: "d02_8k" })).toBeVisible();
  await page.getByRole("button", { name: "All", exact: true }).click();
  const lane = (n: string) => page.locator(`[data-lane="${n}"]`);
  const bars = (n: string) => lane(n).locator("span[title]");
  await expect(bars("spk0").first()).toBeVisible();
  await expect(page.getByText("2 detected by pyannote-diarization")).toBeVisible();
  const n0 = await bars("spk0").count();
  const n1 = await bars("spk1").count();

  // drag a spk0 segment onto the spk1 lane: it becomes spk1's, and stays so after a reload
  const b = (await bars("spk0").first().boundingBox())!;
  const l1 = (await lane("spk1").boundingBox())!;
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2 + 4, l1.y + l1.height / 2, { steps: 6 });
  await page.mouse.up();
  await expect(bars("spk1")).toHaveCount(n1 + 1);
  await expect(bars("spk0")).toHaveCount(n0 - 1);
  await page.reload();
  await expect(bars("spk1")).toHaveCount(n1 + 1);

  // a focused grip nudges with the arrow keys and saves once the keys stop
  const grip = bars("spk1").first().getByRole("button", { name: /^End of segment/ });
  const before = await grip.getAttribute("aria-label");
  await grip.focus();
  await grip.press("ArrowRight");
  await grip.press("ArrowRight");
  await expect(grip).not.toHaveAttribute("aria-label", before!);
  const after = (await grip.getAttribute("aria-label"))!;
  await page.waitForTimeout(1200);
  await page.reload();
  await expect(page.getByRole("button", { name: after, exact: true })).toHaveCount(1);

  // Escape cancels a drag
  const c = (await bars("spk1").first().boundingBox())!;
  await page.mouse.move(c.x + c.width / 2, c.y + c.height / 2);
  await page.mouse.down();
  await page.mouse.move(c.x + c.width / 2 + 80, c.y + c.height / 2, { steps: 4 });
  await page.keyboard.press("Escape");
  await page.mouse.up();
  expect((await bars("spk1").first().boundingBox())!.x).toBeCloseTo(c.x, 0);

  // add a speaker, then draw a segment on its empty lane
  await page.getByRole("button", { name: "Add speaker" }).click();
  await expect(lane("spk2")).toContainText("Drag here to add a segment for spk2, or press 3 on a selected one.");
  await expect(page.getByText("1 added by you")).toBeVisible();
  const l2 = (await lane("spk2").boundingBox())!;
  await page.mouse.move(l2.x + l2.width * 0.1, l2.y + l2.height / 2);
  await page.mouse.down();
  await page.mouse.move(l2.x + l2.width * 0.2, l2.y + l2.height / 2, { steps: 6 });
  await page.mouse.up();
  await expect(bars("spk2")).toHaveCount(1);
  await expect(page.locator("tbody")).toContainText("New · manual");
  expect(errors).toEqual([]);
});

test("lexicons: publish house spellings, then use or keep them in Annotate", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/lexicons");
  await page.getByRole("button", { name: "New lexicon" }).click();
  await page.getByLabel("Name").fill("House style");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page).toHaveURL(/\/lexicons\/\d+$/);
  await expect(page.getByRole("heading", { name: "House style" })).toBeVisible();
  for (const [word, variants] of [["neighbour", "neighbor"], ["midday", "noon"]]) {
    await page.getByLabel("House spelling").fill(word);
    await page.getByLabel("Variants to flag (comma-separated)").fill(variants);
    await page.getByRole("button", { name: "Add entry" }).click();
    await expect(page.getByRole("cell", { name: word, exact: true })).toBeVisible();
  }
  await expect(page.getByText(/Unpublished changes/)).toBeVisible();
  await page.getByRole("button", { name: "Publish v1" }).click();
  await expect(page.getByText("Annotate uses v1.")).toBeVisible();
  // the annotated fixture set's references already say "neighbour"; only the ASR models wrote "neighbor"
  await expect(page.getByRole("row", { name: /neighbour/ })).toContainText(/[1-9]\d* house · 0 variants/);

  await page.goto("/label");
  await page.getByRole("row", { name: /Unlabelled calls/ }).getByRole("link", { name: /Review|Confirm|Check/ }).click();
  await page.getByRole("combobox", { name: "File", exact: true }).selectOption({ index: 3 });
  await expect(page.getByRole("heading", { name: "d04" })).toBeVisible();
  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(page.getByRole("link", { name: /House style v1 · 2 spelling hints/ })).toBeVisible();
  await page.getByRole("button", { name: "neighbor: house spelling is neighbour" }).click();
  const hint = page.getByRole("group", { name: "Spelling hint" });
  await hint.getByRole("button", { name: "Use" }).click();
  // a segment with no hints left is a plain input again
  const transcripts = () => page.getByLabel(/^Transcript at /).evaluateAll((els) => els.map((e) => (e as HTMLInputElement).value).join("\n"));
  await expect.poll(transcripts).toContain("my neighbour if I am out?");
  await expect(page.getByRole("link", { name: /· 1 spelling hint$/ })).toBeVisible();
  await page.getByRole("button", { name: "noon: house spelling is midday" }).click();
  await hint.getByRole("button", { name: "Keep" }).click();
  await expect(page.getByRole("link", { name: /· 0 spelling hints/ })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("link", { name: /· 0 spelling hints/ })).toBeVisible();
  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect.poll(transcripts).toContain("before noon.");
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
  await expect(page.getByRole("table", { name: "Segment outcomes and the rule behind each" }).getByRole("cell", { name: "Unchanged" })).toBeVisible();
  // published lexicons travel with every package (the one made in the lexicons test)
  await expect(page.getByRole("table", { name: "Lexicons in the package" }).getByRole("row", { name: /House style/ })).toContainText("Unchanged");
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
