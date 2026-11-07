// End to end against a real server: the units of a project are edited in the project settings, an
// interface lists its participants and the sender of every message, and the generate panel works
// on one unit at a time.
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, inject, it } from "vitest";
import { Api } from "../src/api";
import { App } from "../src/App";

const PROJECT = "units";

afterEach(() => {
  cleanup();
});

beforeAll(async () => {
  const api = new Api(inject("serverUrl"));
  await api.createProject({ id: PROJECT, name: "units" });
  await api.putValue(PROJECT, "/nodes", { A: { description: "Flight computer" }, B: {} });
  await api.putEntry(PROJECT, "messages", "Command", {
    definition: { id: 1, fields: [{ name: "value", type: "u8" }] },
  });
  await api.putEntry(PROJECT, "interfaces", "link", {
    definition: {
      version: "1.0.0",
      nodes: ["A", "B"],
      header: "none",
      framing: { kind: "none" },
      messages: [{ name: "Command", from: "A" }],
    },
  });
});

async function openProject(user: UserEvent) {
  render(<App api={new Api(inject("serverUrl"))} />);
  await screen.findByRole("option", { name: PROJECT });
  await user.selectOptions(screen.getByLabelText("Project"), PROJECT);
}

async function value(pointer: string): Promise<unknown> {
  const result = await new Api(inject("serverUrl")).value(PROJECT, pointer);
  return (result.data as { value?: unknown }).value;
}

describe("units", () => {
  it("are listed and edited in the project settings", async () => {
    const user = userEvent.setup();
    await openProject(user);
    await user.click(screen.getByRole("button", { name: "Project settings" }));
    const settings = await screen.findByRole("region", { name: "Project settings" });
    await waitFor(() => {
      expect(within(settings).getByLabelText<HTMLInputElement>("Unit 1 name").value).toBe("A");
    });
    expect(within(settings).getByLabelText<HTMLInputElement>("Unit 1 description").value).toBe("Flight computer");
    await user.click(within(settings).getByRole("button", { name: "Add unit" }));
    await user.type(within(settings).getByLabelText("Unit 3 name"), "C");
    await user.selectOptions(within(settings).getByLabelText("Unit 3 language"), "c");
    await user.click(within(settings).getByRole("button", { name: "Save project settings" }));
    await waitFor(async () => {
      expect(await value("/nodes")).toEqual({ A: { description: "Flight computer" }, B: {}, C: { language: "c" } });
    });
  });

  it("take part in an interface and send its messages", async () => {
    const user = userEvent.setup();
    await openProject(user);
    const interfaces = await screen.findByRole("region", { name: "Interfaces" });
    await user.click(await within(interfaces).findByRole("button", { name: "link" }));
    const editor = await screen.findByRole("region", { name: "Interface editor" });
    await waitFor(() => {
      expect(within(editor).getByLabelText<HTMLInputElement>("Message 1 from A").checked).toBe(true);
    });
    expect(within(editor).queryByLabelText("Direction")).toBeNull();
    expect(within(editor).getByLabelText<HTMLInputElement>("B").checked).toBe(true);
    await user.click(within(editor).getByLabelText("Message 1 from B"));
    await user.click(within(editor).getByLabelText(/Count every message/));
    await user.click(within(editor).getByRole("button", { name: "Save interface" }));
    await waitFor(async () => {
      expect(await value("/interfaces/link")).toMatchObject({
        message_counters: true,
        nodes: ["A", "B"],
        messages: [{ name: "Command", from: ["A", "B"] }],
      });
    });
    expect(await value("/interfaces/link")).not.toHaveProperty("direction");
  });

  it("are generated one at a time", async () => {
    const user = userEvent.setup();
    await openProject(user);
    await user.click(screen.getByRole("tab", { name: "Generate" }));
    const panel = await screen.findByRole("region", { name: "Generate" });
    await within(panel).findByRole("option", { name: "units_A/model.hpp" });
    await user.selectOptions(within(panel).getByLabelText("Unit"), "B");
    await within(panel).findByRole("option", { name: "units_B/model.hpp" });
    expect(within(panel).queryByRole("option", { name: "units_A/model.hpp" })).toBeNull();
  });
});
