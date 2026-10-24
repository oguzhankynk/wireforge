// The way from a model to code: the generated files, the download of code and tests, and the start in
// an empty workspace.
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, inject, it, vi } from "vitest";
import { Api, ApiError, type ProjectSummary, type TemplateSummary } from "../src/api";
import { App } from "../src/App";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("generate", () => {
  it("shows every generated file and downloads the code with its tests", async () => {
    const api = new Api(inject("serverUrl"));
    await api.createProject({ id: "gen-demo", name: "gen_demo", template: "telemetry" });
    const user = userEvent.setup();
    render(<App api={api} />);
    await screen.findByRole("option", { name: "gen-demo" });
    await user.selectOptions(screen.getByLabelText("Project"), "gen-demo");
    await user.click(screen.getByRole("tab", { name: "Generate" }));
    const panel = await screen.findByRole("region", { name: "Generate" });
    await within(panel).findByRole("option", { name: "gen_demo/model.hpp" });
    expect(within(panel).getByLabelText<HTMLSelectElement>("File").value).toBe("gen_demo/model.hpp");
    expect(within(panel).getByLabelText("Generated file").textContent).toContain("struct ");

    // jsdom has no object URLs.
    let archive: Blob | undefined;
    const createObjectURL = (object: Blob) => {
      archive = object;
      return "blob:archive";
    };
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: () => undefined, configurable: true });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    await user.click(within(panel).getByRole("button", { name: "Download code and tests" }));
    await waitFor(() => {
      expect(click).toHaveBeenCalled();
    });
    expect(click.mock.contexts[0]).toMatchObject({ download: "gen-demo-generated.zip" });
    expect(archive?.type).toBe("application/zip");
    expect(archive?.size).toBeGreaterThan(1000);

    // The same in C99.
    await user.selectOptions(within(panel).getByLabelText("Language"), "c");
    await within(panel).findByRole("option", { name: "gen_demo/model.c" });
    expect(within(panel).queryByRole("option", { name: "gen_demo/model.hpp" })).toBeNull();
    expect(within(panel).getByLabelText<HTMLSelectElement>("File").value).toBe("gen_demo/model.h");
    await user.click(within(panel).getByRole("button", { name: "Download code and tests" }));
    await waitFor(() => {
      expect(click).toHaveBeenCalledTimes(2);
    });
    expect(click.mock.contexts[1]).toMatchObject({ download: "gen-demo-c-generated.zip" });
  });

  it("reports why a project has no archive", async () => {
    await expect(new Api(inject("serverUrl")).archive("missing")).rejects.toBeInstanceOf(ApiError);
  });

  it("opens the new project form in an empty workspace", async () => {
    const empty = {
      projects: () => Promise.resolve([] as ProjectSummary[]),
      templates: () => Promise.resolve([] as TemplateSummary[]),
    } as unknown as Api;
    render(<App api={empty} />);
    expect(await screen.findByRole("region", { name: "New project" })).toBeTruthy();
  });
});
