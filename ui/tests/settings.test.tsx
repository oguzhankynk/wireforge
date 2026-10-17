// End to end against a real server: project settings, an enum and a message with its own byte
// order, bit order and range checks are created in the UI; editors keep keys they do not show.
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, inject, it } from "vitest";
import { Api } from "../src/api";
import { App } from "../src/App";

const PROJECT = "fresh";

afterEach(() => {
  cleanup();
});

beforeAll(async () => {
  const response = await fetch(`${inject("serverUrl")}/api/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: PROJECT, name: "fresh" }),
  });
  expect(response.status).toBe(201);
});

async function openProject(user: UserEvent) {
  render(<App api={new Api(inject("serverUrl"))} />);
  await screen.findByRole("option", { name: PROJECT });
  await screen.findByRole("button", { name: "nodeB_to_our" });
  await user.selectOptions(screen.getByLabelText("Project"), PROJECT);
}

async function saved(text: string) {
  await waitFor(() => {
    expect(screen.getByLabelText("Save result").textContent).toBe(text);
  });
}

async function value(pointer: string): Promise<unknown> {
  const result = await new Api(inject("serverUrl")).value(PROJECT, pointer);
  return (result.data as { value?: unknown }).value;
}

describe("project settings, enums and message settings", () => {
  it("are edited in the UI", async () => {
    const user = userEvent.setup();
    await openProject(user);

    await user.click(screen.getByRole("button", { name: "Project settings" }));
    const settings = await screen.findByRole("region", { name: "Project settings" });
    await waitFor(() => {
      expect(within(settings).getByLabelText<HTMLInputElement>("Project name").value).toBe("fresh");
    });
    await user.clear(within(settings).getByLabelText("Project version"));
    await user.type(within(settings).getByLabelText("Project version"), "1.2.0");
    await user.type(within(settings).getByLabelText("C++ namespace"), "demo::fresh");
    await user.selectOptions(within(settings).getByLabelText("Byte order"), "little");
    await user.selectOptions(within(settings).getByLabelText("Range check on decode"), "off");
    await user.click(within(settings).getByRole("button", { name: "Add project define" }));
    await user.type(within(settings).getByLabelText("Project define 1 name"), "MAX_ITEMS");
    await user.clear(within(settings).getByLabelText("Project define 1 value"));
    await user.type(within(settings).getByLabelText("Project define 1 value"), "4");
    await user.click(within(settings).getByRole("button", { name: "Save project settings" }));
    await saved("Saved project settings.");
    expect(await value("/")).toMatchObject({
      project: "fresh",
      version: "1.2.0",
      namespace: "demo::fresh",
      defaults: { endian: "little", range_check: { decode: false } },
      defines: { MAX_ITEMS: 4 },
    });

    const enums = screen.getByRole("region", { name: "Enums" });
    await user.click(within(enums).getByRole("button", { name: "New enum" }));
    const enumEditor = await screen.findByRole("region", { name: "Enum editor" });
    await user.type(within(enumEditor).getByLabelText("Enum name"), "Mode");
    await user.type(within(enumEditor).getByLabelText("Member 1 name"), "IDLE");
    await user.click(within(enumEditor).getByRole("button", { name: "Add member" }));
    await user.type(within(enumEditor).getByLabelText("Member 2 name"), "RUN");
    await user.click(within(enumEditor).getByRole("button", { name: "Add member" }));
    await user.type(within(enumEditor).getByLabelText("Member 3 name"), "RUN");
    expect(within(enumEditor).getByRole("list", { name: "Enum problems" }).textContent).toContain("duplicate name");
    expect(within(enumEditor).getByRole<HTMLButtonElement>("button", { name: "Save enum" }).disabled).toBe(true);
    await user.click(within(enumEditor).getByRole("button", { name: "Member 3 remove" }));
    await user.click(within(enumEditor).getByRole("button", { name: "Save enum" }));
    await saved("Saved enums/Mode.");
    expect(await value("/enums/Mode")).toEqual({ type: "u8", values: { IDLE: 0, RUN: 1 } });

    const messages = screen.getByRole("region", { name: "Messages" });
    await user.click(within(messages).getByRole("button", { name: "New message" }));
    let editor = await screen.findByRole("region", { name: "Message editor" });
    await user.type(within(editor).getByLabelText("Message name"), "Status");
    await user.type(within(editor).getByLabelText("Message id"), "0x10");
    await user.selectOptions(within(editor).getByLabelText("Byte order"), "big");
    await user.selectOptions(within(editor).getByLabelText("Bit order"), "lsb_first");
    await user.selectOptions(within(editor).getByLabelText("Range check on encode"), "off");
    await user.clear(within(editor).getByLabelText("Field 1 name"));
    await user.type(within(editor).getByLabelText("Field 1 name"), "mode");
    await waitFor(() => {
      expect(within(editor).getByRole("option", { name: "Mode" })).toBeTruthy();
    });
    await user.selectOptions(within(editor).getByLabelText("Field 1 type"), "Mode");
    await user.click(within(editor).getByRole("button", { name: "Add field" }));
    await user.clear(within(editor).getByLabelText("Field 2 name"));
    await user.type(within(editor).getByLabelText("Field 2 name"), "level");
    await user.selectOptions(within(editor).getByLabelText("Field 2 type"), "u16");
    await user.type(within(editor).getByLabelText("Field 2 max"), "MAX_ITEMS");
    await user.click(within(editor).getByRole("button", { name: "Save message" }));
    await saved("Saved messages/Status.");
    await waitFor(() => {
      expect(screen.getByText("No issues.")).toBeTruthy();
    });
    expect(await value("/messages/Status")).toEqual({
      id: 16,
      endian: "big",
      bit_order: "lsb_first",
      range_check: { encode: false },
      fields: [
        { name: "mode", type: "Mode" },
        { name: "level", type: "u16", max: "MAX_ITEMS" },
      ],
    });

    // Keys the message editor does not show survive an edit made in the UI.
    const api = new Api(inject("serverUrl"));
    await api.project(PROJECT);
    await api.putValue(PROJECT, "/messages/Status/since", "1.2.0");
    cleanup();
    await openProject(user);
    await user.click(await within(screen.getByRole("region", { name: "Messages" })).findByRole("button", { name: "Status" }));
    editor = await screen.findByRole("region", { name: "Message editor" });
    await waitFor(() => {
      expect(within(editor).getByLabelText<HTMLSelectElement>("Bit order").value).toBe("lsb_first");
    });
    await user.type(within(editor).getByLabelText("Description"), "Device status.");
    await user.selectOptions(within(editor).getByLabelText("Range check on encode"), "");
    await user.click(within(editor).getByRole("button", { name: "Save message" }));
    await saved("Saved messages/Status.");
    expect(await value("/messages/Status")).toEqual({
      id: 16,
      description: "Device status.",
      endian: "big",
      bit_order: "lsb_first",
      since: "1.2.0",
      fields: [
        { name: "mode", type: "Mode" },
        { name: "level", type: "u16", max: "MAX_ITEMS" },
      ],
    });
  });

  it("keeps interface keys the editor does not show", async () => {
    const api = new Api(inject("serverUrl"));
    await api.project(PROJECT);
    await api.putEntry(PROJECT, "messages", "LinkPayload", {
      definition: { id: 1, fields: [{ name: "value", type: "u8" }] },
    });
    await api.putEntry(PROJECT, "interfaces", "link", {
      definition: {
        version: "1.0.0",
        direction: "rx",
        bit_order: "lsb_first",
        header: "none",
        framing: { kind: "none" },
        messages: ["LinkPayload"],
      },
    });
    const user = userEvent.setup();
    await openProject(user);
    const interfaces = screen.getByRole("region", { name: "Interfaces" });
    await user.click(await within(interfaces).findByRole("button", { name: "link" }));
    const editor = await screen.findByRole("region", { name: "Interface editor" });
    await waitFor(() => {
      expect(within(editor).getByLabelText<HTMLSelectElement>("Direction").value).toBe("rx");
      expect(within(editor).getByLabelText<HTMLSelectElement>("Message 1 name").value).toBe("LinkPayload");
    });
    await waitFor(() => {
      expect(within(editor).getByLabelText<HTMLInputElement>("Version").value).toBe("1.0.0");
    });
    await user.click(within(editor).getByLabelText(/Allow trailing bytes/));
    await user.click(within(editor).getByRole("button", { name: "Save interface" }));
    await saved("Saved interfaces/link.");
    expect(await value("/interfaces/link")).toMatchObject({
      bit_order: "lsb_first",
      allow_trailing_bytes: true,
    });
  });
});
