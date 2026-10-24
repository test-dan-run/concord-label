import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { SHORTCUTS } from "../../lib/label";
import { DocsPage } from "./DocsPage";

function page() {
  return render(<MemoryRouter><DocsPage /></MemoryRouter>);
}

const sectionShown = (name: string) => !screen.getByRole("heading", { name, hidden: true }).closest("section")!.hidden;

describe("DocsPage", () => {
  afterEach(cleanup);

  it("lists every Annotate shortcut", () => {
    page();
    for (const k of SHORTCUTS) expect(screen.getByText(k.does)).toBeTruthy();
  });

  it("search keeps the sections that mention every word", () => {
    page();
    act(() => { fireEvent.change(screen.getByLabelText("Search the docs"), { target: { value: "collar DER" } }); });
    expect(sectionShown("Live evaluation")).toBe(true);
    expect(sectionShown("Datasets")).toBe(false);
    const nav = screen.getByRole("navigation", { name: "On this page" });
    expect(nav.textContent).toBe("On this pageLive evaluation");
  });

  it("says so when nothing matches", () => {
    page();
    act(() => { fireEvent.change(screen.getByLabelText("Search the docs"), { target: { value: "zebra" } }); });
    expect(screen.getByText(`No section mentions "zebra".`)).toBeTruthy();
  });
});
