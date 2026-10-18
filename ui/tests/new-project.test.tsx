// A new user starts a project from a template in the UI against a real server.
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, inject, it } from "vitest";
import { Api } from "../src/api";
import { App } from "../src/App";

afterEach(() => {
  cleanup();
});

describe("new project", () => {
  it("creates a working project from a template", async () => {
    const user = userEvent.setup();
    render(<App api={new Api(inject("serverUrl"))} />);
    await screen.findByRole("option", { name: "avionics" });
    await user.click(screen.getByRole("button", { name: "New project" }));
    const form = await screen.findByRole("region", { name: "New project" });
    await screen.findByRole("option", { name: "command" });
    const create = screen.getByRole("button", { name: "Create project" });
    expect((create as HTMLButtonElement).disabled).toBe(true);
    await user.selectOptions(screen.getByLabelText("Template"), "command");
    await user.type(screen.getByLabelText("Project id (directory)"), "first-link");
    await user.type(screen.getByLabelText("Project name (C++ identifier)"), "first_link");
    expect((create as HTMLButtonElement).disabled).toBe(false);
    await user.click(create);
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "New project" })).toBeNull();
    });
    expect(form.isConnected).toBe(false);
    await screen.findByRole("option", { name: "first-link" });
    expect((screen.getByLabelText<HTMLSelectElement>("Project")).value).toBe("first-link");
    const messages = screen.getByRole("region", { name: "Messages" });
    await waitFor(() => {
      expect(messages.textContent).toContain("Command");
    });
    expect(screen.getByLabelText("Save result").textContent).toBe("Created first-link.");
  });

  it("shows the refusal of an existing project", async () => {
    const user = userEvent.setup();
    render(<App api={new Api(inject("serverUrl"))} />);
    await screen.findByRole("option", { name: "avionics" });
    await user.click(screen.getByRole("button", { name: "New project" }));
    await screen.findByRole("option", { name: "raw" });
    await user.type(screen.getByLabelText("Project id (directory)"), "avionics");
    await user.type(screen.getByLabelText("Project name (C++ identifier)"), "again");
    await user.click(screen.getByRole("button", { name: "Create project" }));
    expect((await screen.findByRole("alert")).textContent).toContain("already exists");
  });
});
