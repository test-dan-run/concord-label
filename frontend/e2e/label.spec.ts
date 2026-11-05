import * as fs from "node:fs";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Concord Label on the replayed fixture set: tests/e2e_server.py lists the fixture models from the replay catalog, uploads the
// annotated fixture set ("Synth fixtures") and an audio-only copy of its audio ("Unlabelled calls"), and
// pseudo-labels the copy with pyannote -> both Whispers. It also uploads "Short clips", with one transcript per clip,
// and "Labelled clips", the same with uploaded labels, and pseudo-labels both with both Whispers. Console errors fail the test.
// With the Dataset Catalog set up (a FakeDatasetCatalog, unless E2E_DATASET_CATALOG=0), the dataset pages publish to
// it; without it they build reference packages to download. The catalog also holds "Raw calls" (v1 audio only, made into a Label dataset,
// then v2 from Concord Bench). The tests of the path that isn't set up are skipped.

async function catalogOn(request: APIRequestContext): Promise<boolean> {
  const cfg = (await (await request.get("/api/config")).json()) as { dataset_catalog?: { enabled: boolean } };
  return cfg.dataset_catalog?.enabled === true;
}

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
  await expect(page.getByLabel(/Skip a segment when an ASR model fails on it/)).toBeChecked();
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
  // a click edits the text, and Enter saves it
  await page.locator("[data-transcript]").first().click();
  const first = page.getByLabel(/^Transcript at /);
  await first.fill("Edited in the browser.");
  await first.press("Enter");
  await expect(first).toHaveCount(0);
  await expect(page.locator("[data-transcript]").first()).toHaveText("Edited in the browser.");
  await page.reload();
  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(page.locator("[data-transcript]").first()).toHaveText("Edited in the browser.");
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
  const pill = page.locator("tbody tr").nth(3).getByTitle(/Change the speaker/);
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
  await expect(lane("spk2")).toContainText("Drag or double-click here to add a segment for spk2, or press 3 on a selected one.");
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

test("annotate: Space pauses what it plays, from a play button or a row checkbox too", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/label");
  await page.getByRole("row", { name: /Unlabelled calls/ }).getByRole("link", { name: /Review|Confirm|Check/ }).click();
  await page.getByRole("combobox", { name: "File", exact: true }).selectOption({ index: 5 });
  await page.getByRole("button", { name: "All", exact: true }).click();
  const audio = page.locator("audio").first();
  const paused = () => audio.evaluate((a: HTMLAudioElement) => a.paused);
  // Space pauses a segment started from its play button (which keeps the focus), and stays paused
  await page.getByRole("button", { name: /^Play segment at / }).nth(1).click();
  await expect.poll(paused, { timeout: 15_000 }).toBe(false);
  await page.keyboard.press("Space");
  await expect.poll(paused).toBe(true);
  await page.waitForTimeout(300);
  expect(await paused()).toBe(true);
  // with a row's checkbox focused, Space plays and pauses instead of ticking it
  await page.getByLabel(/^Select segment at /).nth(1).click();
  await page.keyboard.press("Space");
  await expect.poll(paused, { timeout: 15_000 }).toBe(false);
  await page.keyboard.press("Space");
  await expect.poll(paused).toBe(true);
  await expect(page.getByLabel(/^Select segment at /).nth(1)).toBeChecked();
  expect(errors).toEqual([]);
});

test("annotate: pull a stretch out on the waveform and play it, across speakers", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/label");
  await page.getByRole("row", { name: /Unlabelled calls/ }).getByRole("link", { name: /Review|Confirm|Check/ }).click();
  await page.getByRole("combobox", { name: "File", exact: true }).selectOption({ index: 5 });
  await expect(page.getByText("Drag across the waveform to play a stretch of the file.")).toBeVisible();
  const audio = page.locator("audio").first();
  const state = () => audio.evaluate((a: HTMLAudioElement) => ({ paused: a.paused, t: a.currentTime }));
  const wave = (await page.locator("canvas").first().boundingBox())!;
  const y = wave.y + wave.height / 2;
  // drag from 20 % to 25 % of the file: the stretch plays at once and stops at its end
  await page.mouse.move(wave.x + wave.width * 0.2, y);
  await page.mouse.down();
  await page.mouse.move(wave.x + wave.width * 0.25, y, { steps: 5 });
  await page.mouse.up();
  const sel = page.getByRole("group", { name: "Waveform selection" });
  await expect(sel).toContainText(/Selection\s*\d\d:\d\d\.\d{3} – \d\d:\d\d\.\d{3} · \d\.\d\d s/);
  const [a, b] = (await sel.textContent())!.match(/\d\d:\d\d\.\d{3}/g)!.map((c) => Number(c.slice(0, 2)) * 60 + Number(c.slice(3)));
  await expect.poll(async () => (await state()).paused, { timeout: 15_000 }).toBe(false);
  expect((await state()).t).toBeGreaterThanOrEqual(a - 0.01);
  await expect.poll(async () => (await state()).paused, { timeout: 15_000 }).toBe(true);
  expect((await state()).t).toBeGreaterThanOrEqual(b - 0.01);
  expect((await state()).t).toBeLessThan(b + 0.25);
  // Shift+Space plays it again from its start, and Space pauses it
  await page.keyboard.press("Shift+Space");
  await expect.poll(async () => (await state()).paused).toBe(false);
  expect((await state()).t).toBeLessThan(b);
  await page.keyboard.press("Space");
  await expect.poll(async () => (await state()).paused).toBe(true);
  // Escape clears it; so does a click on the waveform
  await page.keyboard.press("Escape");
  await expect(sel).toHaveCount(0);
  await page.mouse.move(wave.x + wave.width * 0.5, y);
  await page.mouse.down();
  await page.mouse.move(wave.x + wave.width * 0.55, y, { steps: 5 });
  await page.mouse.up();
  await expect(sel).toBeVisible();
  await page.keyboard.press("Space");
  await page.mouse.click(wave.x + wave.width * 0.8, y);
  await expect(sel).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("annotate: zoom the timeline for fine edits", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/label");
  await page.getByRole("row", { name: /Unlabelled calls/ }).getByRole("link", { name: /Review|Confirm|Check/ }).click();
  await page.getByRole("combobox", { name: "File", exact: true }).selectOption({ index: 4 });
  await expect(page.getByRole("heading", { name: "d05_clipped" })).toBeVisible();
  const zoom = page.getByRole("group", { name: "Timeline zoom" });
  await expect(zoom).toContainText("Whole file");
  const bar = page.locator('[data-lane="spk0"] span[title]').first();
  const w0 = (await bar.boundingBox())!.width;

  // a button step doubles the scale around the playhead (at 0 s, so the first segment stays in sight)
  await zoom.getByRole("button", { name: "Zoom in (=)" }).click();
  await expect(zoom).not.toContainText("Whole file");
  expect((await bar.boundingBox())!.width / w0).toBeCloseTo(2, 1);

  // Ctrl + scroll zooms further at the pointer; 0 shows the whole file again
  const lane = (await page.locator('[data-lane="spk0"]').boundingBox())!;
  await page.mouse.move(lane.x + 40, lane.y + lane.height / 2);
  await page.keyboard.down("Control");
  await page.mouse.wheel(0, -400);
  await page.keyboard.up("Control");
  await expect.poll(async () => (await bar.boundingBox())!.width / w0).toBeGreaterThan(3);
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("0");
  await expect(zoom).toContainText("Whole file");

  // zoomed in to a few seconds, a 60 px drag of a grip moves it by a fraction of a second
  for (let k = 0; k < 4; k++) await page.keyboard.press("=");
  await expect(zoom).toContainText("00:00.000 – 00:02.331 · 2.3 s"); // a sixteenth of the 37.3 s file
  await bar.hover();
  const grip = bar.getByRole("button", { name: /^Start of segment/ });
  const before = await grip.getAttribute("aria-label");
  const g = (await grip.boundingBox())!;
  await page.mouse.move(g.x + g.width / 2, g.y + g.height / 2);
  await page.mouse.down();
  await page.mouse.move(g.x + g.width / 2 + 60, g.y + g.height / 2, { steps: 6 });
  await page.mouse.up();
  await expect(grip).not.toHaveAttribute("aria-label", before!);
  const secs = (label: string) => { const m = /(\d+):(\d+\.\d+)\./.exec(label)!; return Number(m[1]) * 60 + Number(m[2]); };
  const moved = secs((await grip.getAttribute("aria-label"))!) - secs(before!);
  expect(moved).toBeGreaterThan(0.05);
  expect(moved).toBeLessThan(0.5);

  // the overview strip pans the window: drag it to the right
  const thumb = page.locator("div.cursor-pointer.touch-none > span.cursor-grab");
  const t = (await thumb.boundingBox())!;
  const text = await zoom.textContent();
  await page.mouse.move(t.x + t.width / 2, t.y + t.height / 2);
  await page.mouse.down();
  await page.mouse.move(t.x + t.width / 2 + 200, t.y + t.height / 2, { steps: 5 });
  await page.mouse.up();
  await expect(zoom).not.toHaveText(text!);
  expect(errors).toEqual([]);
});

test("annotate: the transport's speed, a wheel pan, and a click that ends a segment's playback", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/label");
  await page.getByRole("row", { name: /Unlabelled calls/ }).getByRole("link", { name: /Review|Confirm|Check/ }).click();
  await page.getByRole("combobox", { name: "File", exact: true }).selectOption({ index: 4 });
  await expect(page.getByRole("heading", { name: "d05_clipped" })).toBeVisible();
  const audio = page.locator("audio").first();
  // the speed applies at once and carries over to the next page load
  await page.getByLabel("Playback speed").selectOption("1.5");
  await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.playbackRate)).toBe(1.5);
  await page.reload();
  await expect(page.getByLabel("Playback speed")).toHaveValue("1.5");
  await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.playbackRate)).toBe(1.5);
  await page.getByLabel("Playback speed").selectOption("1");

  // zoomed in, a plain wheel over the lanes pans the timeline and leaves the page where it was
  const zoom = page.getByRole("group", { name: "Timeline zoom" });
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("=");
  await page.keyboard.press("=");
  await expect(zoom).toContainText("00:00.000 – ");
  const lane = (await page.locator('[data-lane="spk0"]').boundingBox())!;
  const scrollY = await page.evaluate(() => window.scrollY);
  await page.mouse.move(lane.x + lane.width / 2, lane.y + lane.height / 2);
  await page.mouse.wheel(0, 300);
  await expect(zoom).not.toContainText("00:00.000 – ");
  expect(await page.evaluate(() => window.scrollY)).toBe(scrollY);
  await page.keyboard.press("0");

  // a segment started with its play button stops when you click somewhere else on the waveform
  const paused = () => audio.evaluate((a: HTMLAudioElement) => a.paused);
  await page.getByRole("button", { name: "All", exact: true }).click();
  await page.getByRole("button", { name: /^Play segment at / }).nth(1).click();
  await expect.poll(paused, { timeout: 15_000 }).toBe(false);
  await expect(page.locator("tbody tr[data-playing]")).toHaveCount(1);
  const wave = (await page.locator("canvas").first().boundingBox())!;
  await page.mouse.click(wave.x + wave.width * 0.9, wave.y + wave.height / 2);
  await expect.poll(paused).toBe(true);
  expect(await audio.evaluate((a: HTMLAudioElement) => a.currentTime)).toBeGreaterThan(30);
  expect(errors).toEqual([]);
});

test("annotate: undo a merge with Ctrl+Z, and redo it", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/label");
  await page.getByRole("row", { name: /Unlabelled calls/ }).getByRole("link", { name: /Review|Confirm|Check/ }).click();
  await page.getByRole("combobox", { name: "File", exact: true }).selectOption({ index: 2 });
  await expect(page.getByRole("heading", { name: "d03_44k_stereo" })).toBeVisible();
  await page.getByRole("button", { name: "All", exact: true }).click();
  const rows = page.locator("tbody tr");
  const history = page.getByRole("group", { name: "History" });
  await expect(rows.first()).toBeVisible();
  const n = await rows.count();
  const texts = await page.locator("[data-transcript]").allTextContents();
  const boxes = page.getByLabel(/^Select segment at /);
  await boxes.nth(0).click();
  await boxes.nth(1).click();
  await page.getByRole("toolbar", { name: "Bulk actions" }).getByRole("button", { name: "Merge" }).click();
  await expect(rows).toHaveCount(n - 1);
  await expect(history.getByRole("button", { name: "Undo" })).toHaveAttribute("title", "Undo merge (Ctrl+Z)");
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("Control+z");
  await expect(rows).toHaveCount(n);
  await expect(page.getByText("Undid merge")).toBeVisible();
  await expect.poll(() => page.locator("[data-transcript]").allTextContents()).toEqual(texts);
  await expect(history.getByRole("button", { name: "Redo" })).toHaveAttribute("title", "Redo merge (Ctrl+Shift+Z)");
  await page.keyboard.press("Control+Shift+z");
  await expect(rows).toHaveCount(n - 1);
  await expect(page.getByText("Redid merge")).toBeVisible();
  await page.keyboard.press("Control+z");
  await expect(rows).toHaveCount(n);
  expect(errors).toEqual([]);
});

test("annotate: the keys", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/label");
  await page.getByRole("row", { name: /Unlabelled calls/ }).getByRole("link", { name: /Review|Confirm|Check/ }).click();
  await page.getByRole("combobox", { name: "File", exact: true }).selectOption({ index: 5 });
  await expect(page.getByRole("heading", { name: "d06" })).toBeVisible();
  await page.getByRole("button", { name: "All", exact: true }).click();
  const audio = page.locator("audio").first();
  const t = () => audio.evaluate((a: HTMLAudioElement) => a.currentTime);
  const paused = () => audio.evaluate((a: HTMLAudioElement) => a.paused);
  const rows = page.locator("tbody tr");
  const current = page.locator("tbody tr[aria-current]");
  const start = async (row: typeof current) => {
    const m = /(\d\d):(\d\d\.\d{3})/.exec((await row.locator("td").nth(2).textContent())!)!;
    return Number(m[1]) * 60 + Number(m[2]);
  };
  await page.locator("body").click({ position: { x: 5, y: 5 } });

  // ↑ and ↓ select the previous or next segment and move the playhead to its start
  await rows.nth(0).click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("ArrowDown");
  const second = rows.nth(1);
  await expect(second).toHaveAttribute("aria-current", "true");
  await expect.poll(t).toBeCloseTo(await start(second), 2);
  await page.keyboard.press("ArrowUp");
  await expect(rows.nth(0)).toHaveAttribute("aria-current", "true");
  // ← and → skip 2 seconds
  const t0 = await t();
  await page.keyboard.press("ArrowRight");
  await expect.poll(t).toBeCloseTo(t0 + 2, 2);
  await page.keyboard.press("ArrowLeft");
  await expect.poll(t).toBeCloseTo(t0, 2);
  // Space plays from the playhead and pauses; Shift+Space plays the selected segment to its end
  await page.keyboard.press("Space");
  await expect.poll(paused, { timeout: 15_000 }).toBe(false);
  await page.keyboard.press("Space");
  await expect.poll(paused).toBe(true);
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Shift+Space");
  await expect.poll(paused, { timeout: 15_000 }).toBe(false);
  await expect.poll(paused, { timeout: 15_000 }).toBe(true);
  // the side panel shows the selected segment's span, "00:01.651 – 00:02.832 · 1.18 s"
  const m2 = /\d\d:\d\d\.\d{3} – (\d\d):(\d\d\.\d{3})/.exec((await page.getByText(/^\d\d:\d\d\.\d{3} – \d\d:\d\d\.\d{3} · /).textContent())!)!;
  expect(await t()).toBeGreaterThanOrEqual(Number(m2[1]) * 60 + Number(m2[2]) - 0.01);

  // Tab and Shift+Tab go to the next and previous segment to review
  await page.keyboard.press("Tab");
  await expect(current).toContainText("Needs review");
  const first = await current.getAttribute("aria-label") ?? (await current.textContent());
  await page.keyboard.press("Tab");
  await expect(current).toContainText("Needs review");
  await expect(current).not.toHaveText(first!);
  await page.keyboard.press("Shift+Tab");
  await expect(current).toHaveText(first!);
  // Tab keeps its usual meaning in the header
  await page.getByRole("combobox", { name: "File", exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Previous file" })).toBeFocused();
  await expect(current).toHaveText(first!);

  // Enter edits the text: Esc cancels, Enter saves
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  const shown = current.locator("[data-transcript]");
  const text = (await shown.textContent())!;
  await page.keyboard.press("Enter");
  const input = current.getByLabel(/^Transcript at /);
  await expect(input).toBeFocused();
  await page.keyboard.type(" (draft)");
  await page.keyboard.press("Escape");
  await expect(input).toHaveCount(0);
  await expect(shown).toHaveText(text);
  await page.keyboard.press("Enter");
  await page.keyboard.type(" Checked.");
  await page.keyboard.press("Enter");
  await expect(shown).toHaveText(`${text} Checked.`);
  // Ctrl+Enter confirms it and goes to the next segment to review
  const edited = rows.filter({ hasText: `${text} Checked.` });
  await page.keyboard.press("Control+Enter");
  await expect(edited).toContainText("Confirmed");
  await expect(current).not.toHaveText(await edited.textContent() ?? "");

  // S splits the segment under the playhead; V does nothing now
  const n = await rows.count();
  const bar = page.locator('[data-lane] span[title]').first();
  await bar.click();
  await page.keyboard.press("v");
  await page.waitForTimeout(300);
  await expect(rows).toHaveCount(n);
  await page.keyboard.press("s");
  await expect(rows).toHaveCount(n + 1);
  // M merges it back with the next one, Delete deletes the selected segment, and Ctrl+Z and Ctrl+Y undo and redo
  await page.keyboard.press("m");
  await expect(rows).toHaveCount(n);
  await page.keyboard.press("Delete");
  await expect(rows).toHaveCount(n - 1);
  await page.keyboard.press("Control+z");
  await expect(rows).toHaveCount(n);
  await page.keyboard.press("Control+y");
  await expect(rows).toHaveCount(n - 1);
  await page.keyboard.press("Control+Shift+z");
  await page.keyboard.press("Control+z");
  await expect(rows).toHaveCount(n);

  // Esc clears the selected rows first, then the waveform selection
  const boxes = page.getByLabel(/^Select segment at /);
  await boxes.nth(0).click();
  await boxes.nth(1).click();
  const wave = (await page.locator("canvas").first().boundingBox())!;
  await page.mouse.move(wave.x + wave.width * 0.6, wave.y + wave.height / 2);
  await page.mouse.down();
  await page.mouse.move(wave.x + wave.width * 0.65, wave.y + wave.height / 2, { steps: 5 });
  await page.mouse.up();
  const sel = page.getByRole("group", { name: "Waveform selection" });
  await expect(sel).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("toolbar", { name: "Bulk actions" })).toHaveCount(0);
  await expect(sel).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sel).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("annotate: the segment list reads like pseudolabel's, and Tab plays the next segment to review", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/label");
  await page.getByRole("row", { name: /Unlabelled calls/ }).getByRole("link", { name: /Review|Confirm|Check/ }).click();
  await page.getByRole("combobox", { name: "File", exact: true }).selectOption({ index: 4 });
  await expect(page.getByRole("heading", { name: "d05_clipped" })).toBeVisible();
  await page.getByRole("button", { name: "All", exact: true }).click();
  const rows = page.locator("tbody tr");
  const audio = page.locator("audio").first();
  const t = () => audio.evaluate((a: HTMLAudioElement) => a.currentTime);

  // a click on the start time moves the playhead there
  const row = rows.nth(2);
  await row.getByTitle("Move the playhead to the start of the segment").click();
  await expect(row).toHaveAttribute("aria-current", "true");
  const at = (await row.getByTitle("Move the playhead to the start of the segment").textContent())!;
  await expect.poll(t).toBeCloseTo(Number(at.slice(0, 2)) * 60 + Number(at.slice(3)), 2);

  // a single click on the text opens the editor with the cursor where you clicked; with word timings, a
  // click on a word also moves the playhead to it while the audio is paused
  const shown = row.locator("[data-transcript]");
  const text = (await shown.textContent())!;
  const word = row.locator("[data-transcript] [data-word]").nth(2);
  const wordText = (await word.textContent())!;
  const before = await t();
  await word.click({ position: { x: 1, y: 5 } });
  await expect.poll(t).toBeGreaterThan(before);
  const editor = row.getByLabel(/^Transcript at /);
  await expect(editor).toBeFocused();
  const caret = await editor.evaluate((el: HTMLTextAreaElement) => el.selectionStart);
  expect(caret - text.indexOf(wordText)).toBeGreaterThanOrEqual(0);
  expect(caret - text.indexOf(wordText)).toBeLessThanOrEqual(1);
  await page.keyboard.press("Escape");
  await expect(editor).toHaveCount(0);

  // Enter saves, and Enter opens the editor again
  await shown.click();
  await expect(editor).toBeFocused();
  await page.keyboard.press("Control+End");
  await page.keyboard.type(" Twice.");
  await page.keyboard.press("Enter");
  await expect(shown).toHaveText(`${text} Twice.`);
  await page.keyboard.press("Enter");
  await expect(editor).toBeFocused();
  await editor.fill(text);
  await page.keyboard.press("Enter");
  await expect(shown).toHaveText(text);

  // the speaker chip opens a menu: pick another speaker, or a new one
  const chip = row.getByTitle(/Change the speaker/);
  const was = (await chip.textContent())!.trim();
  await chip.click();
  const menu = page.getByRole("menu", { name: "Speaker" });
  await expect(menu.getByRole("menuitem")).toHaveCount(4); // spk0, spk1, No speaker, New speaker
  await menu.getByRole("menuitem", { name: was === "spk0" ? /spk1/ : /spk0/ }).click();
  await expect(menu).toHaveCount(0);
  await expect(chip).toHaveText(was === "spk0" ? "spk1" : "spk0");
  await chip.click();
  await menu.getByRole("menuitem", { name: "New speaker" }).click();
  await expect(chip).toHaveText("spk2");
  await expect(page.locator('[data-lane="spk2"]')).toBeVisible();
  await chip.click();
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);

  await expect(page.getByRole("group", { name: "Review" })).toContainText("Nothing left to review");
  // Ctrl+Enter confirms the segment and goes to the next one that isn't confirmed, auto-accepted ones too
  await row.click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("Control+Enter");
  await expect(row).toContainText("Confirmed");
  await expect(rows.nth(3)).toHaveAttribute("aria-current", "true");
  await expect(rows.nth(3)).toContainText("Auto-accepted");

  // Tab goes to the next segment to review and plays it from start to end (d06 has one left after the keys test)
  await page.getByRole("combobox", { name: "File", exact: true }).selectOption({ index: 5 });
  await expect(page.getByRole("heading", { name: "d06" })).toBeVisible();
  await expect(page.getByRole("group", { name: "Review" })).toContainText(/[12] to review/);
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("Tab");
  const current = page.locator("tbody tr[aria-current]");
  await expect(current).toContainText(/Needs review|New · manual/);
  await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.paused), { timeout: 15_000 }).toBe(false);
  const start = (await current.getByTitle("Move the playhead to the start of the segment").textContent())!;
  expect(await t()).toBeGreaterThanOrEqual(Number(start.slice(0, 2)) * 60 + Number(start.slice(3)) - 0.01);
  await page.keyboard.press("Space");
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
  const transcripts = async () => (await page.locator("[data-transcript]").allTextContents()).join("\n");
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

test("annotate: drag a shared edge, rename a lane, and double-click to add a segment", async ({ page, request }) => {
  const errors = watchErrors(page);
  type Seg = { id: number; start: number; end: number; speaker: string | null; status: string };
  const ds = ((await (await request.get("/api/datasets")).json()) as { id: number; name: string }[]).find((d) => d.name === "Unlabelled calls")!;
  const file = ((await (await request.get(`/api/label/datasets/${ds.id}/files`)).json()) as { id: number; stem: string; duration: number }[]).find((f) => f.stem === "d04")!;
  const view = async () => (await (await request.get(`/api/label/files/${file.id}`)).json()) as { segments: Seg[] };
  const live = (await view()).segments.filter((x) => x.status !== "rejected");
  // two segments of different speakers made to touch: the first now ends where the second starts
  const k = live.findIndex((x, j) => live[j + 1] && live[j + 1].speaker !== x.speaker && live[j + 1].start - x.start > 1);
  const [a, b] = [live[k], live[k + 1]];
  expect((await request.patch(`/api/label/segments/${a.id}`, { data: { end: b.start } })).ok()).toBe(true);
  const clk = (t: number) => `${String(Math.floor(t / 60)).padStart(2, "0")}:${(t % 60).toFixed(3).padStart(6, "0")}`;
  await page.goto(`/label/annotate/${ds.id}?file=${file.id}`);
  await expect(page.getByRole("heading", { name: "d04" })).toBeVisible();
  const bar = (x: Seg) => page.locator(`[data-lane="${x.speaker}"] span[title*="${clk(x.start)} – "]`);
  const after = async () => Object.fromEntries((await view()).segments.map((x) => [x.id, x]));

  // dragging the end of the first moves the start of the second with it, as one edit
  await bar(a).hover();
  const grip = bar(a).getByRole("button", { name: /^End of segment/ });
  const g = (await grip.boundingBox())!;
  await page.mouse.move(g.x + g.width / 2, g.y + g.height / 2);
  await page.mouse.down();
  await page.mouse.move(g.x + g.width / 2 - 25, g.y + g.height / 2, { steps: 5 });
  await page.mouse.up();
  await expect.poll(async () => (await after())[a.id].end).toBeLessThan(b.start);
  const moved = await after();
  expect(moved[b.id].start).toBe(moved[a.id].end);
  await expect(page.getByRole("group", { name: "History" }).getByRole("button", { name: "Undo" })).toHaveAttribute("title", "Undo boundary move (Ctrl+Z)");
  // with Alt, the edge moves alone
  const g2 = (await grip.boundingBox())!;
  await page.keyboard.down("Alt");
  await page.mouse.move(g2.x + g2.width / 2, g2.y + g2.height / 2);
  await page.mouse.down();
  await page.mouse.move(g2.x + g2.width / 2 - 25, g2.y + g2.height / 2, { steps: 5 });
  await page.mouse.up();
  await page.keyboard.up("Alt");
  await expect.poll(async () => (await after())[a.id].end).toBeLessThan(moved[a.id].end);
  expect((await after())[b.id].start).toBe(moved[b.id].start);

  // a double-click on an empty part of a lane adds a 2 second segment there
  const spk = a.speaker!;
  const mine = (await view()).segments.filter((x) => x.speaker === spk && x.status !== "rejected").sort((p, q) => p.start - q.start);
  const edges = [0, ...mine.flatMap((x) => [x.start, x.end]), file.duration];
  let gap = 0;
  for (let j = 0; j < edges.length; j += 2) if (edges[j + 1] - edges[j] > 3) { gap = (edges[j] + edges[j + 1]) / 2; break; }
  expect(gap).toBeGreaterThan(0);
  const l = (await page.locator(`[data-lane="${spk}"]`).boundingBox())!;
  const n = await page.locator(`[data-lane="${spk}"] span[title]`).count();
  await page.mouse.dblclick(l.x + (l.width * gap) / file.duration, l.y + l.height / 2);
  await expect(page.locator(`[data-lane="${spk}"] span[title]`)).toHaveCount(n + 1);
  const added = (await view()).segments.find((x) => x.speaker === spk && x.start < gap && gap < x.end)!;
  expect(added.end - added.start).toBeCloseTo(2, 1);

  // a double-click on a lane's name renames the speaker on every segment; Esc cancels
  const other = b.speaker!;
  await page.getByTitle(`${other} (double-click to rename)`, { exact: true }).locator("span.truncate").dblclick();
  await page.getByLabel(`Rename ${other}`).fill("Caller");
  await page.keyboard.press("Escape");
  await expect(page.getByLabel(`Rename ${other}`)).toHaveCount(0);
  await page.getByTitle(`${other} (double-click to rename)`, { exact: true }).locator("span.truncate").dblclick();
  await page.getByLabel(`Rename ${other}`).fill("Caller");
  await page.keyboard.press("Enter");
  await expect(page.locator('[data-lane="Caller"]')).toBeVisible();
  await expect(page.locator(`[data-lane="${other}"]`)).toHaveCount(0);
  expect((await view()).segments.filter((x) => x.speaker === other)).toEqual([]);
  // and the rename can be undone
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("Control+z");
  await expect(page.locator(`[data-lane="${other}"]`)).toBeVisible();
  expect(errors).toEqual([]);
});

test("annotate: adding a segment keeps the list where you are working", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/label");
  await page.getByRole("row", { name: /Unlabelled calls/ }).getByRole("link", { name: /Review|Confirm|Check/ }).click();
  await page.getByRole("combobox", { name: "File", exact: true }).selectOption({ index: 0 });
  await expect(page.getByRole("heading", { name: "d01" })).toBeVisible();
  await page.getByRole("button", { name: "All", exact: true }).click();
  const list = page.locator("div.overflow-auto:has(> table)");
  const rows = page.locator("tbody tr");
  const n = await rows.count();
  // work near the end of the file: select a late segment, so the list scrolls down to it
  await rows.nth(n - 2).click({ position: { x: 5, y: 5 } });
  await expect.poll(() => list.evaluate((el) => el.scrollTop)).toBeGreaterThan(100);
  // add a segment late in the file with a double-click on an empty part of a lane
  await page.locator("[data-lane]").first().scrollIntoViewIfNeeded();
  const lane = (await page.locator("[data-lane]").first().boundingBox())!;
  const bars = await page.locator("[data-lane]").first().locator("span[title]").evaluateAll((els) => els.map((e) => e.getBoundingClientRect()).map((r) => [r.left, r.right]));
  let x = 0;
  for (let k = bars.length - 1; k > 0 && !x; k--) if (bars[k][0] - bars[k - 1][1] > 40) x = (bars[k][0] + bars[k - 1][1]) / 2;
  expect(x).toBeGreaterThan(lane.x + lane.width / 2);
  await page.mouse.dblclick(x, lane.y + lane.height / 2);
  await expect(rows).toHaveCount(n + 1);
  // the new segment is selected, and the list stays down there instead of jumping to the top
  await expect(page.locator("tbody tr[aria-current]")).toContainText("New · manual");
  await page.waitForTimeout(300);
  expect(await list.evaluate((el) => el.scrollTop)).toBeGreaterThan(100);
  // the same for a segment added at the playhead
  await rows.nth(n - 3).getByTitle("Move the playhead to the start of the segment").click();
  await page.getByRole("button", { name: "Segment at playhead" }).click();
  await expect(rows).toHaveCount(n + 2);
  await page.waitForTimeout(300);
  expect(await list.evaluate((el) => el.scrollTop)).toBeGreaterThan(100);
  expect(errors).toEqual([]);
});

test("live evaluation loads standings and DER; Publish or Download, no locked score without Bench", async ({ page, request }) => {
  const errors = watchErrors(page);
  const catalog = await catalogOn(request);
  // confirm every segment of one file, so cpWER has a fully reviewed file to score
  const ds = ((await (await request.get("/api/datasets")).json()) as { id: number; name: string }[]).find((d) => d.name === "Unlabelled calls")!;
  const file = ((await (await request.get(`/api/label/datasets/${ds.id}/files`)).json()) as { id: number; stem: string }[]).find((f) => f.stem === "d02_8k")!;
  const ids = ((await (await request.get(`/api/label/files/${file.id}`)).json()) as { segments: { id: number }[] }).segments.map((x) => x.id);
  expect((await request.post("/api/label/segments/bulk", { data: { ids, action: "confirm" } })).ok()).toBe(true);
  await page.goto("/label/live");
  await page.getByRole("main").getByRole("link", { name: /Unlabelled calls/ }).click();
  await expect(page.getByRole("heading", { name: "Live evaluation" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "WER vs confirmed segments" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Standings" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Diarisation (DER)" })).toBeVisible();
  // cpWER sits beside WER, with the diariser's speakers, on each file up to where its review has got
  await expect(page.getByRole("columnheader", { name: "cpWER" })).toBeVisible();
  await expect(page.getByText(/with the speakers pyannote-diarization found\. .* of audio in [1-9]\d* of 6 files so far, [1-9]\d* of them reviewed to the end/)).toBeVisible();
  const row = page.getByRole("row", { name: /whisper-large-v3/ }).first();
  await expect(row.getByRole("cell").nth(4)).toHaveText(/\d+\.\d%/);
  // Publish to Dataset Catalog replaces Promote and Send to Bench; without the catalog the panel builds a package to
  // download. Without BENCH_URL the locked score is hidden.
  await expect(page.getByRole("heading", { name: "Promote to benchmark dataset" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Send to Bench" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: catalog ? "Publish to Dataset Catalog" : "Download references" })).toBeVisible();
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

test("without the Dataset Catalog: download a reference package, listed in the history", async ({ page, request }) => {
  test.skip(await catalogOn(request), "the Dataset Catalog is set up, so the panel publishes there");
  const errors = watchErrors(page);
  await page.goto("/datasets");
  await page.getByRole("link", { name: "Synth fixtures" }).click();
  const panel = page.locator("section").filter({ has: page.getByRole("heading", { name: "Download references" }) });
  await expect(panel.getByLabel("Package name")).toHaveValue("Synth fixtures");
  await expect(panel.getByText("speaker + text (SegLST)")).toBeVisible();
  await expect(panel.getByText("condition", { exact: true })).toBeVisible(); // file labels for Bench (CONTRACTS.md §2.7)
  await panel.getByLabel("Package name").fill("Synth refs");
  await panel.getByRole("button", { name: "Build package" }).click();
  const link = panel.getByRole("link", { name: /^Download synth-refs-refs-v\d+\.zip/ });
  await expect(link).toBeVisible({ timeout: 30_000 });
  const [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
  expect(download.suggestedFilename()).toMatch(/^synth-refs-refs-v\d+\.zip$/);
  const file = await download.path();
  expect(fs.statSync(file!).size).toBeGreaterThan(1000);
  expect(fs.readFileSync(file!).subarray(0, 2).toString()).toBe("PK");
  const history = page.locator("section").filter({ has: page.getByRole("heading", { name: "Reference packages" }) });
  await expect(history.getByRole("row", { name: /v\d+ complete .*download done/ }).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("publish to the Dataset Catalog, then open the version as a new dataset", async ({ page, request }) => {
  test.skip(!(await catalogOn(request)), "the Dataset Catalog is not set up");
  const errors = watchErrors(page);
  await page.goto("/datasets");
  await page.getByRole("link", { name: "Synth fixtures" }).click();
  const panel = page.locator("section").filter({ has: page.getByRole("heading", { name: "Publish to Dataset Catalog" }) });
  await expect(panel.getByLabel("Name")).toHaveValue("Synth fixtures");
  await expect(panel.getByText("speaker + text (SegLST)")).toBeVisible();
  await expect(panel.getByText("new, made on the first publish")).toBeVisible();
  await expect(panel.getByText("Include audio")).toHaveCount(0);
  await panel.getByLabel("Name").fill("Synth refs");
  await panel.getByRole("button", { name: "Publish to Dataset Catalog" }).click();
  const v1 = panel.getByRole("link", { name: "v1", exact: true });
  await expect(v1).toBeVisible({ timeout: 30_000 });
  await expect(v1).toHaveAttribute("href", /\/datasets\/[0-9a-f]{32}\/v\/1$/);
  await expect(panel.getByText("Based on")).toBeVisible();
  const history = page.locator("section").filter({ has: page.getByRole("heading", { name: "Published versions" }) });
  const row = history.getByRole("row", { name: /v1 complete .*Dataset Catalog.*done/ });
  await expect(row).toBeVisible();
  await expect(row.getByRole("link", { name: "Open v1 ↗" })).toHaveAttribute("href", /\/datasets\/[0-9a-f]{32}\/v\/1$/);
  await expect(page.getByText("based on v1 ↗")).toBeVisible();

  // New from Dataset Catalog makes a working dataset from v1
  await page.goto("/datasets");
  await page.getByRole("link", { name: "New from Dataset Catalog" }).click();
  await expect(page.getByRole("heading", { name: "New from Dataset Catalog" })).toBeVisible();
  const table = page.getByRole("table", { name: "Datasets in the Dataset Catalog" });
  await expect(table.getByRole("row", { name: /Raw calls/ })).toContainText("audio only");
  await table.getByRole("button", { name: "Synth refs", exact: true }).click();
  await expect(page.getByLabel("Version")).toContainText("v1 · 6 files · ASR + diarisation");
  await page.getByLabel("Name", { exact: true }).fill("Synth refs from the catalog");
  await page.getByRole("button", { name: "Make dataset" }).click();
  await expect(page.getByRole("heading", { name: "Synth refs from the catalog" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Report" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("6 usable of 6")).toBeVisible();
  await expect(page.getByText("based on v1 ↗")).toBeVisible();
  await expect(page.getByRole("link", { name: "Open in annotator" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("publish asks first when someone published a newer version", async ({ page, request }) => {
  test.skip(!(await catalogOn(request)), "the Dataset Catalog is not set up");
  const errors = watchErrors(page);
  await page.goto("/datasets");
  await page.getByRole("link", { name: "Raw calls" }).click();
  await expect(page.getByRole("main").getByRole("link", { name: "Pseudo-label" })).toBeVisible();
  const panel = page.locator("section").filter({ has: page.getByRole("heading", { name: "Publish to Dataset Catalog" }) });
  const alert = panel.getByRole("alert");
  await expect(alert).toContainText(/v2 was published by concord-bench on .*, after v1, the version this dataset is based on/);
  await expect(alert.getByLabel("Publish anyway")).not.toBeChecked();
  await expect(panel.getByText("v2 by concord-bench")).toBeVisible();
  expect(errors).toEqual([]);
});

test("open in Label by sha256: the annotator opens on the file at t", async ({ page, request }) => {
  const errors = watchErrors(page);
  const datasets = (await (await request.get("/api/datasets")).json()) as { id: number; name: string; catalog_dataset_id: string | null }[];
  const ds = datasets.find((d) => d.name === "Synth fixtures")!;
  const files = (await (await request.get(`/api/datasets/${ds.id}/files`)).json()) as { id: number; sha256: string; status: string; stem: string }[];
  const f = files.find((x) => x.status === "ok")!;
  // Bench and the Dataset Catalog UI link with the catalog dataset id (published above when the catalog is set up)
  const q = ds.catalog_dataset_id ? `catalog_dataset_id=${ds.catalog_dataset_id}&` : "";
  const hit = (await (await request.get(`/api/files/by-sha/${f.sha256}?${q}`)).json()) as { file_id: number; dataset_id: number };
  const view = (await (await request.get(`/api/label/files/${hit.file_id}`)).json()) as { segments: { start: number; end: number; text: string | null }[] };
  const seg = view.segments[1];
  const t = Math.round(((seg.start + seg.end) / 2) * 100) / 100;
  await page.goto(`/label/file/by-sha/${f.sha256}?${q}t=${t}`);
  await expect(page).toHaveURL(new RegExp(`/label/annotate/${hit.dataset_id}\\?file=${hit.file_id}&t=`));
  await expect(page.getByRole("heading", { name: f.stem, exact: true })).toBeVisible();
  await expect(page.locator('tbody tr[aria-current="true"] [data-transcript]')).toHaveText(seg.text ?? "(no transcript)");
  // an unknown fingerprint says so
  await page.goto(`/label/file/by-sha/${"0".repeat(64)}`);
  await expect(page.getByText(/This audio is not in Concord Label/)).toBeVisible();
  // a catalog dataset no Label dataset was made from points at New from Dataset Catalog
  await page.goto(`/label/file/by-sha/${f.sha256}?catalog_dataset_id=${"f".repeat(32)}`);
  await expect(page.getByText(/in a dataset made from that Dataset Catalog dataset/)).toBeVisible();
  await expect(page.getByRole("link", { name: "New from Dataset Catalog" })).toBeVisible();
  expect(errors.filter((e) => !e.includes("404"))).toEqual([]);
});

// Last: these confirm ASR text across "Short clips", which the lexicon test above would count as variants.
test.describe("clips", () => {
  type ClipRow = { id: number; stem: string; duration: number; segments: number; segment: { id: number; text: string | null; status: string } | null };
  const dataset = async (request: APIRequestContext) => {
    const all = (await (await request.get("/api/datasets")).json()) as { id: number; name: string }[];
    return all.find((d) => d.name === "Short clips")!.id;
  };
  const clips = async (request: APIRequestContext, ds: number) => ((await (await request.get(`/api/label/datasets/${ds}/clips`)).json()) as { clips: ClipRow[] }).clips;
  const list = (page: Page) => page.getByRole("list", { name: "Clips" });
  const row = (page: Page, stem: string) => list(page).getByRole("listitem").filter({ hasText: stem });

  test("annotate: one transcript for each clip, walked from the keyboard", async ({ page, request }) => {
    const errors = watchErrors(page);
    const ds = await dataset(request);
    const before = await clips(request, ds);
    expect(before.every((c) => c.segments === 1 && c.segment?.status !== "confirmed")).toBe(true);
    await page.goto(`/label/annotate/${ds}`);
    await expect(page.getByRole("heading", { name: "Transcript" })).toBeVisible();
    await expect(list(page).getByRole("listitem")).toHaveCount(before.length);
    // nothing about speakers or splitting
    for (const name of ["Add speaker", "Split", "Merge", "Delete"]) await expect(page.getByRole("button", { name, exact: true })).toHaveCount(0);
    await expect(page.getByRole("group", { name: "Focus" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "One transcript per clip" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("Speaker", { exact: true })).toHaveCount(0);
    const [first, second, third] = before;
    await expect(page.getByRole("heading", { name: first.stem })).toBeVisible();

    // Ctrl+Enter confirms and goes on, and the next clip plays
    await page.keyboard.press("Control+Enter");
    await expect(page.getByRole("heading", { name: second.stem })).toBeVisible();
    await expect(row(page, first.stem)).toContainText("Confirmed");
    await expect(page.getByRole("button", { name: "Pause" })).toBeVisible();
    await expect(page.getByLabel("Playhead")).not.toContainText("0:00 /", { timeout: 15_000 });

    // Ctrl+Space plays and pauses while typing, and the space goes nowhere; Ctrl+Enter saves, confirms and goes on
    await page.keyboard.press("Enter");
    const editor = page.getByRole("textbox", { name: /^Transcript at/ });
    await expect(editor).toBeFocused();
    await page.keyboard.press("Control+Space");
    await expect(page.getByRole("button", { name: "Play", exact: true }).first()).toBeVisible();
    await editor.fill("a corrected transcript");
    await page.keyboard.press("Control+Space");
    await expect(page.getByRole("button", { name: "Pause" })).toBeVisible();
    await expect(editor).toHaveValue("a corrected transcript");
    await page.keyboard.press("Control+Enter");
    await expect(page.getByRole("heading", { name: third.stem })).toBeVisible();
    await expect.poll(async () => (await clips(request, ds)).find((c) => c.id === second.id)?.segment).toMatchObject({ text: "a corrected transcript", status: "confirmed" });

    // ↑ goes back, and undo works on the clip on the screen
    await page.keyboard.press("ArrowUp");
    await expect(page.getByRole("heading", { name: second.stem })).toBeVisible();
    await expect(page.locator("[data-transcript]")).toContainText("a corrected transcript");
    await expect(page.getByRole("button", { name: "Undo" })).toBeEnabled();
    await page.keyboard.press("Control+z");
    await expect(page.getByText("Undid confirm")).toBeVisible();
    await expect(row(page, second.stem)).not.toContainText("Confirmed");
    await page.keyboard.press("Control+Shift+z");
    await expect(page.getByText("Redid confirm")).toBeVisible();
    await expect(row(page, second.stem)).toContainText("Confirmed");

    // R rejects and goes on, and a rejected clip stays decided
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("heading", { name: third.stem })).toBeVisible();
    await page.keyboard.press("r");
    await expect(row(page, third.stem)).toContainText("Rejected");
    await expect(page.getByRole("heading", { name: before[3].stem })).toBeVisible();

    // the filter and the search narrow the list; a click there opens the clip
    await page.getByLabel("Show").selectOption("confirmed");
    await expect(list(page).getByRole("listitem")).toHaveCount(2);
    await page.getByLabel("Show").selectOption("all");
    await page.getByLabel("Search clips by name or transcript").fill("a corrected");
    await expect(list(page).getByRole("listitem")).toHaveCount(1);
    await list(page).getByRole("button").click();
    await expect(page.getByRole("heading", { name: second.stem })).toBeVisible();
    await page.getByLabel("Search clips by name or transcript").fill("");
    expect(errors).toEqual([]);
  });

  test("annotate: a clip with no transcript, and a clip with several segments", async ({ page, request }) => {
    const errors = watchErrors(page);
    const ds = await dataset(request);
    const [blank, split] = (await clips(request, ds)).slice(-2);
    await request.delete(`/api/label/segments/${blank.segment!.id}`);
    await request.delete(`/api/label/segments/${split.segment!.id}`);
    for (const [start, end, text] of [[0, 1, "first part"], [1, 2, "second part"]]) {
      await request.post(`/api/label/files/${split.id}/segments`, { data: { start, end, text } });
    }

    // Tab reaches the blank clip with the editor ready, and confirming needs text
    await page.goto(`/label/annotate/${ds}?file=${(await clips(request, ds))[0].id}`);
    await expect(page.getByRole("heading", { name: "Transcript" })).toBeVisible();
    await expect(page.getByRole("group", { name: "Review" })).toContainText("to review");
    await expect(row(page, blank.stem)).toContainText("No transcript");
    await page.keyboard.press("Shift+Tab");
    await expect(page.getByRole("heading", { name: split.stem })).toBeVisible();
    await page.keyboard.press("Shift+Tab");
    await expect(page.getByRole("heading", { name: blank.stem })).toBeVisible();
    const editor = page.getByRole("textbox", { name: /^Transcript at/ });
    await expect(editor).toBeFocused();
    await page.keyboard.press("Control+Enter");
    await expect(page.getByText(/Type a transcript before you confirm/)).toBeVisible();
    await expect(page.getByRole("heading", { name: blank.stem })).toBeVisible();

    // text saved with Ctrl+Enter makes one segment over the whole clip, and confirms it
    await page.getByRole("button", { name: /No transcript yet/ }).click();
    await page.getByRole("textbox", { name: /^Transcript at/ }).fill("typed from nothing");
    await page.keyboard.press("Control+Enter");
    await expect(page.getByRole("heading", { name: split.stem })).toBeVisible();
    await expect.poll(async () => (await clips(request, ds)).find((c) => c.id === blank.id)?.segment).toMatchObject({ text: "typed from nothing", status: "confirmed" });
    const view = (await (await request.get(`/api/label/files/${blank.id}`)).json()) as { segments: { start: number; end: number; speaker: string | null }[] };
    expect(view.segments).toHaveLength(1);
    expect(view.segments[0]).toMatchObject({ start: 0, speaker: null });
    expect(Math.abs(view.segments[0].end - blank.duration)).toBeLessThan(0.002);

    // several segments are never joined on their own: confirm does nothing, and the page offers a merge
    const several = page.getByRole("group", { name: "Several segments" });
    await expect(several).toContainText("first part");
    await expect(several).toContainText("second part");
    await expect(row(page, split.stem)).toContainText("2 segments");
    await page.keyboard.press("Control+Enter");
    await expect(page.getByRole("heading", { name: split.stem })).toBeVisible();
    await expect(several.getByRole("link", { name: "Open with speakers" })).toHaveAttribute("href", new RegExp(`view=speakers&file=${split.id}`));
    await several.getByRole("button", { name: "Merge into one" }).click();
    await expect(page.locator("[data-transcript]")).toContainText("first part second part");
    await expect(row(page, split.stem)).not.toContainText("2 segments");

    // the link to the speaker view, and back again without changing the dataset
    await page.goto(`/label/annotate/${ds}?view=speakers&file=${split.id}`);
    await expect(page.getByRole("button", { name: "Add speaker" })).toBeVisible();
    await page.getByRole("button", { name: "One transcript per clip" }).click();
    await expect(page.getByRole("heading", { name: "Transcript" })).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("a clip dataset: confirm all, pseudo-label without a segmenter, live evaluation without speakers, and the switch", async ({ page, request }) => {
    const errors = watchErrors(page);
    const ds = await dataset(request);
    await page.goto(`/label/pseudo/${ds}`);
    await expect(page.getByRole("button", { name: "One transcript per clip" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByLabel("Segments from")).toHaveCount(0);
    await expect(page.getByText("Speakers", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Re-fuse 2 saved outputs" })).toBeEnabled();
    await expect(page.getByLabel(/Skip a clip when an ASR model fails on it/)).toBeChecked();

    // a re-run leaves the clips people reviewed
    const reviewed = (await clips(request, ds)).filter((c) => c.segment?.status === "confirmed");
    expect(reviewed.length).toBeGreaterThan(0);
    const before = await page.getByText(/ from run \d+/).textContent();
    await page.getByRole("button", { name: "Re-fuse 2 saved outputs" }).click();
    await expect(page.getByText(/ from run \d+/)).not.toHaveText(before!, { timeout: 30_000 });
    const after = await clips(request, ds);
    for (const c of reviewed) expect(after.find((x) => x.id === c.id)?.segment).toMatchObject({ id: c.segment!.id, text: c.segment!.text, status: "confirmed" });
    expect(after.every((c) => c.segments <= 1)).toBe(true);

    await page.goto(`/label/annotate/${ds}`);
    const all = page.getByRole("button", { name: /Confirm all high-confidence/ });
    const auto = after.filter((c) => c.segment?.status === "auto").length;
    await expect(all).toContainText(`(${auto})`);
    page.once("dialog", (d) => void d.accept());
    await all.click();
    await expect(all).toContainText("(0)");
    expect((await clips(request, ds)).filter((c) => c.segment?.status === "auto")).toHaveLength(0);

    await page.goto(`/label/live/${ds}`);
    await expect(page.getByRole("heading", { name: "Standings" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "WER", exact: true })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "cpWER" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: /Diarisation/ })).toHaveCount(0);

    // the switch brings the speaker view and the segmenter back, and it changes no segments
    const segments = await clips(request, ds);
    await page.goto(`/label/annotate/${ds}`);
    await page.getByRole("button", { name: "Speaker turns" }).click();
    await expect(page.getByRole("button", { name: "Add speaker" })).toBeVisible();
    expect(await clips(request, ds)).toEqual(segments);
    await page.goto(`/label/pseudo/${ds}`);
    await expect(page.getByLabel("Segments from")).toBeVisible();
    await page.getByRole("button", { name: "One transcript per clip" }).click();
    await expect(page.getByLabel("Segments from")).toHaveCount(0);
    expect(errors).toEqual([]);
  });
});

// Beside the "clips" group, because it fails requests on purpose and so does not watch for console errors.
test("annotate clips: a failed save never confirms, and an undone transcript can't be confirmed", async ({ page, request }) => {
  const all = (await (await request.get("/api/datasets")).json()) as { id: number; name: string }[];
  const ds = all.find((d) => d.name === "Short clips")!.id;
  type Row = { id: number; stem: string; segment: { id: number; text: string | null; status: string } | null };
  const clips = async () => ((await (await request.get(`/api/label/datasets/${ds}/clips`)).json()) as { clips: Row[] }).clips;
  const [blank, kept] = await clips();
  await request.delete(`/api/label/segments/${blank.segment!.id}`);
  await request.post("/api/label/segments/bulk", { data: { ids: [kept.segment!.id], action: "review" } });

  // text saved and then undone leaves nothing to confirm
  await page.goto(`/label/annotate/${ds}?file=${blank.id}`);
  await page.getByRole("button", { name: /No transcript yet/ }).click();
  await page.getByRole("textbox", { name: /^Transcript at/ }).fill("typed then undone");
  await page.keyboard.press("Enter");
  await expect(page.locator("[data-transcript]")).toContainText("typed then undone");
  await page.keyboard.press("Control+z");
  await expect(page.getByRole("button", { name: /No transcript yet/ })).toBeVisible();
  await page.keyboard.press("Control+Enter");
  await expect(page.getByText(/Type a transcript before you confirm/)).toBeVisible();
  expect((await clips()).find((c) => c.id === blank.id)?.segment).toBeNull();

  // a failed save on a clip with no transcript confirms nothing, and the error keeps the text
  await page.route("**/api/label/files/*/segments", (r) => r.fulfill({ status: 500, json: { detail: "disk full" } }));
  await page.getByRole("button", { name: /No transcript yet/ }).click();
  await page.getByRole("textbox", { name: /^Transcript at/ }).fill("words that did not save");
  await page.keyboard.press("Control+Enter");
  await expect(page.getByText(/wasn't saved.*words that did not save/)).toBeVisible();
  expect((await clips()).find((c) => c.id === blank.id)?.segment).toBeNull();
  await page.unroute("**/api/label/files/*/segments");

  // a failed edit doesn't confirm the old text
  await page.route("**/api/label/segments/*", (r) => (r.request().method() === "PATCH" ? r.fulfill({ status: 500, json: { detail: "disk full" } }) : r.continue()));
  await page.goto(`/label/annotate/${ds}?file=${kept.id}`);
  await expect(page.locator("[data-transcript]")).toBeVisible();
  await page.keyboard.press("Enter");
  await page.getByRole("textbox", { name: /^Transcript at/ }).fill("an edit that did not save");
  await page.keyboard.press("Control+Enter");
  await expect(page.getByText(/wasn't saved.*an edit that did not save/)).toBeVisible();
  const after = (await clips()).find((c) => c.id === kept.id)!.segment!;
  expect(after).toMatchObject({ text: kept.segment!.text, status: "pseudo" });
});

test("upload: choose how the dataset is labelled", async ({ page }) => {
  await page.goto("/datasets/upload");
  const clipsChoice = page.getByRole("radio", { name: /One transcript per clip/ });
  const turnsChoice = page.getByRole("radio", { name: /Speaker turns/ });
  await expect(turnsChoice).toBeChecked();
  await clipsChoice.check();
  await expect(clipsChoice).toBeChecked();
  await page.getByRole("radio", { name: /Audio only/ }).click();
  await expect(clipsChoice).toBeChecked();
});

test("annotate clips: filter by agreement, and find the clips where only the label differs", async ({ page, request }) => {
  const errors = watchErrors(page);
  type Row = { id: number; stem: string; segment: { confidence: number | null; reviewed: boolean; uploaded_differs: boolean | null } };
  const all = (await (await request.get("/api/datasets")).json()) as { id: number; name: string }[];
  const ds = all.find((d) => d.name === "Labelled clips")!.id;
  const clips = ((await (await request.get(`/api/label/datasets/${ds}/clips`)).json()) as { clips: Row[] }).clips;
  const stems = (rows: Row[]) => rows.map((c) => c.stem);
  const low = [...clips].sort((a, b) => a.segment.confidence! - b.segment.confidence!);
  const rows = page.getByRole("list", { name: "Clips" }).getByRole("listitem");
  const shown = async () => (await rows.locator("span.num.text-xs").allInnerTexts()).map((t) => t.trim());
  const lowest = page.getByLabel("Lowest agreement in percent");
  const highest = page.getByLabel("Highest agreement in percent");
  const uploaded = page.getByRole("combobox", { name: /^Uploaded label/ });
  await page.goto(`/label/annotate/${ds}`);
  await expect(rows).toHaveCount(clips.length);
  await expect(uploaded).toHaveValue("any");

  // the worst clip first, and the page goes there
  await page.getByLabel("Order").selectOption("low");
  await expect.poll(shown).toEqual(stems(low));
  await expect(page).toHaveURL(/order=low/);
  await expect(page.getByRole("heading", { name: low.find((c) => !c.segment.reviewed)!.stem })).toBeVisible();

  // a range: only what is in it, and the walking keys stay inside it
  const limit = Math.round(100 * low[0].segment.confidence!);
  const inRange = low.filter((c) => Math.round(100 * c.segment.confidence!) <= limit);
  await highest.fill(String(limit));
  await expect.poll(shown).toEqual(stems(inRange));
  await expect(page.getByRole("heading", { name: `Clips · ${inRange.length}` })).toBeVisible();
  await expect(rows.first().getByTitle("Agreement between the models")).toHaveText(`${limit}%`);
  await expect(page).toHaveURL(new RegExp(`agree_to=${limit}`));
  await page.locator("body").click({ position: { x: 1, y: 1 } });
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("heading", { name: inRange[inRange.length > 1 ? 1 : 0].stem })).toBeVisible();

  // the filter stays through a reload, and goes when cleared
  await page.reload();
  await expect.poll(shown).toEqual(stems(inRange));
  await expect(highest).toHaveValue(String(limit));
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(rows).toHaveCount(clips.length);
  await expect(page).not.toHaveURL(/agree_to/);
  await expect(page).toHaveURL(/order=low/); // an order isn't a filter

  // nothing above 100%: a limit that nothing meets says so
  await lowest.fill("100");
  await highest.fill("0");
  await expect(page.getByText("No clips match.")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();

  // the label filters count the clips nobody has checked, and "only" needs the models to agree
  const differs = low.filter((c) => c.segment.uploaded_differs && !c.segment.reviewed);
  const only = differs.filter((c) => c.segment.confidence! >= 0.85);
  await expect(uploaded.locator("option[value=differs]")).toHaveText(`Label differs (${differs.length})`);
  await expect(uploaded.locator("option[value=only]")).toHaveText(`Models agree, label differs (${only.length})`);
  await uploaded.selectOption("differs");
  await expect.poll(shown).toEqual(stems(differs));
  await expect(rows.getByText("Only label differs")).toHaveCount(only.length);
  await uploaded.selectOption("only");
  await expect.poll(shown).toEqual(stems(only));
  await expect(page).toHaveURL(/label=only/);
  expect(only.length).toBeGreaterThan(0);
  await expect(page.getByRole("heading", { name: only[0].stem })).toBeVisible();
  await expect(page.getByText(new RegExp(`differs?, and the models agree \\d+%`))).toBeVisible();

  // a click in the list, then Tab plays the next clip inside the filter
  if (only.length > 1) {
    await rows.first().getByRole("button").click();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("heading", { name: only[1].stem })).toBeVisible();
  }
  await expect(page.getByText(`${only.length} to review`)).toBeVisible();
  expect(errors).toEqual([]);
});

test("annotate: filter a file's segments by agreement, and go to the next file in range", async ({ page, request }) => {
  const errors = watchErrors(page);
  type Seg = { id: number; confidence: number | null };
  type File = { id: number; stem: string; usable: boolean; agreement: Record<string, number> };
  const all = (await (await request.get("/api/datasets")).json()) as { id: number; name: string }[];
  const ds = all.find((d) => d.name === "Unlabelled calls")!.id;
  const files = ((await (await request.get(`/api/label/datasets/${ds}/files`)).json()) as File[]).filter((f) => f.usable && Object.keys(f.agreement).length);
  expect(files.length).toBeGreaterThan(1);
  const segsOf = async (id: number) => ((await (await request.get(`/api/label/files/${id}`)).json()) as { segments: Seg[] }).segments;
  // a limit that keeps part of the first file, and leaves segments in another file too
  const pcts = Object.keys(files[0].agreement).map(Number).sort((a, b) => a - b);
  const limit = pcts[0];
  const segs = await segsOf(files[0].id);
  const kept = segs.filter((s) => s.confidence !== null && Math.round(100 * s.confidence) >= limit).length;
  await page.goto(`/label/annotate/${ds}?file=${files[0].id}`);
  const table = page.locator("tbody tr");
  await expect(table.first()).toBeVisible();
  await page.getByLabel("Lowest agreement in percent").fill(String(limit));
  await expect(page.getByRole("heading", { name: `Segments · ${kept} of ${segs.length}` })).toBeVisible();
  await expect(table).toHaveCount(kept);

  // lowest first in the table; the timeline keeps every segment
  await page.getByLabel("Order").selectOption("low");
  const shownPct = async () => (await table.getByText(/^\d+%$/).allInnerTexts()).map((t) => parseInt(t, 10));
  await expect.poll(async () => { const p = await shownPct(); return p.every((x, i) => i === 0 || p[i - 1] <= x); }).toBe(true);

  // the filter comes along to the next file, and the file menu counts what is in range
  await expect(page.getByLabel("File", { exact: true })).toContainText("to check in range");
  await page.getByRole("button", { name: "Next file in range ›" }).click();
  await expect(page).toHaveURL(new RegExp(`file=(?!${files[0].id}\\b)\\d+`));
  await expect(page).toHaveURL(new RegExp(`agree_from=${limit}`));
  await expect(page.getByRole("heading", { name: /Segments · \d+ of \d+/ })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.getByRole("heading", { name: /Segments · \d+(?! of)/ })).toBeVisible();
  expect(errors).toEqual([]);
});

test("annotate clips: pseudo-labels checked against uploaded labels", async ({ page, request }) => {
  const errors = watchErrors(page);
  type Row = { id: number; stem: string; segment: { id: number; text: string; status: string } };
  const all = (await (await request.get("/api/datasets")).json()) as { id: number; name: string }[];
  const ds = all.find((d) => d.name === "Labelled clips")!.id;
  const clips = async () => ((await (await request.get(`/api/label/datasets/${ds}/clips`)).json()) as { clips: Row[] }).clips;
  const before = await clips();
  // the clip whose label says what the models said is auto-accepted, and nothing is confirmed on its own
  expect(before.every((c) => c.segment.status !== "confirmed")).toBe(true);
  const match = before.find((c) => c.segment.status === "auto")!;
  const differ = before.find((c) => c.segment.status === "pseudo")!;

  await page.goto(`/label/annotate/${ds}?file=${differ.id}`);
  const panel = page.getByRole("heading", { name: "Uploaded label" });
  await expect(panel).toBeVisible();
  await expect(page.getByText(/\d+ words? differs?/)).toBeVisible();
  await expect(page.locator("[data-uploaded] mark").first()).toBeVisible();
  await expect(page.getByRole("button", { name: /Keep all uploaded labels/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Confirm all auto-accepted/ })).toBeVisible();
  const label = (await page.locator("[data-uploaded]").innerText()).trim();
  expect(label).not.toBe(differ.segment.text.trim());

  // U takes the uploaded label, and Ctrl+Enter confirms it
  await page.keyboard.press("u");
  await expect(page.getByText("Same words as the transcript")).toBeVisible();
  await page.keyboard.press("Control+Enter");
  await expect.poll(async () => (await clips()).find((c) => c.id === differ.id)?.segment.status).toBe("confirmed");
  expect((await clips()).find((c) => c.id === differ.id)?.segment.text).toBe(label);

  await page.goto(`/label/annotate/${ds}?file=${match.id}`);
  await expect(page.getByText("Same words as the transcript")).toBeVisible();
  await expect(page.locator("[data-uploaded] mark")).toHaveCount(0);

  // keeping all of them restores each label, after asking
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: /Keep all uploaded labels/ }).click();
  await expect(page.getByRole("button", { name: /Keep all uploaded labels/ })).toHaveCount(0);
  expect((await clips()).every((c) => c.segment.status === "confirmed")).toBe(true);
  expect(errors).toEqual([]);
});
