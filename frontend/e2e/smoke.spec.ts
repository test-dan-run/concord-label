import { expect, test, type Page } from "@playwright/test";

// Every Concord Label screen outside the labelling pages loads on the replayed fixture set
// (tests/e2e_server.py). Console errors fail the test.

function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error" && !m.text().includes("favicon")) errors.push(m.text());
  });
  return errors;
}

test("the app opens on the console; the nav has Label's pages only", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await expect(page).toHaveURL(/\/label$/);
  await expect(page).toHaveTitle("Concord Label");
  const nav = page.getByRole("navigation", { name: "Primary" });
  for (const name of ["Console", "Pseudo-label", "Annotate", "Live evaluation", "Datasets", "Sync", "Models", "Jobs", "Offline export"]) {
    await expect(nav.getByRole("link", { name, exact: true })).toBeVisible();
  }
  for (const name of ["Benchmarks", "Error analysis", "Training"]) {
    await expect(nav.getByRole("link", { name })).toHaveCount(0);
  }
  // pages that moved to Concord Bench (or Improve, on hold) fall back to the console
  for (const path of ["/benchmarks", "/errors", "/training"]) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/label$/);
  }
  expect(errors).toEqual([]);
});

test("datasets, upload, models, contract, jobs and offline export load", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/datasets");
  await expect(page.getByRole("link", { name: "Synth fixtures" })).toBeVisible();
  await page.getByRole("link", { name: "Unlabelled calls" }).click();
  await expect(page.getByText("Flagged files")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Pseudo-label runs on this dataset" })).toBeVisible();
  await expect(page.getByText(/Pseudo-label · 2 ASR models/).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Run evaluation" })).toHaveCount(0);
  await page.goto("/datasets/upload");
  await expect(page.getByRole("heading", { name: "Upload dataset" })).toBeVisible();
  await expect(page.getByRole("link", { name: "asr.jsonl" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Benchmark plan" })).toHaveCount(0);
  await page.goto("/models/add?name=pyannote-diarization");
  await expect(page.getByText("OIP signature")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Benchmark on" })).toHaveCount(0);
  await page.goto("/models");
  await expect(page.getByText("faster-whisper-large-v3 · openai / audio.transcriptions")).toBeVisible();
  await page.goto("/contract");
  await expect(page.getByText("How capabilities and timing are detected")).toBeVisible();
  await page.goto("/jobs");
  await expect(page.getByRole("heading", { name: "Jobs" })).toBeVisible();
  expect(errors).toEqual([]);
  // The offline page asks the platform's stack.py for its services, which the e2e container does not
  // have (a 503 the page shows); it still loads.
  await page.goto("/offline");
  await expect(page.getByRole("heading", { name: "Export for offline" })).toBeVisible();
});
