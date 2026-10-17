// Acceptance: a nested struct with bit-fields, an optional field, a dynamic array and a CRC is
// created in the UI against a real server, validates and generates C++.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { afterEach, describe, expect, inject, it } from "vitest";
import { cleanup } from "@testing-library/react";
import { Api } from "../src/api";
import { App } from "../src/App";

afterEach(() => {
  cleanup();
});

interface Row {
  name: string;
  type?: string;
  occurs?: string;
  cells?: Record<string, string>;
  checksum?: string;
  covers?: string;
}

async function fillRow(user: UserEvent, editor: HTMLElement, index: number, row: Row) {
  const label = `Field ${String(index + 1)}`;
  const name = within(editor).getByLabelText(`${label} name`);
  await user.clear(name);
  await user.type(name, row.name);
  if (row.type !== undefined) await user.selectOptions(within(editor).getByLabelText(`${label} type`), row.type);
  if (row.occurs !== undefined) {
    await user.selectOptions(within(editor).getByLabelText(`${label} occurs`), row.occurs);
  }
  for (const [cell, value] of Object.entries(row.cells ?? {})) {
    await user.type(within(editor).getByLabelText(`${label} ${cell}`), value);
  }
  if (row.checksum !== undefined) {
    await user.selectOptions(within(editor).getByLabelText(`${label} checksum`), row.checksum);
  }
  if (row.covers !== undefined) {
    await user.selectOptions(within(editor).getByLabelText(`${label} covers`), row.covers);
  }
}

async function createStruct(user: UserEvent, kind: "type" | "message", name: string, rows: Row[], id?: string) {
  await user.click(screen.getByRole("button", { name: `New ${kind}` }));
  const editor = await screen.findByRole("region", { name: kind === "type" ? "Type editor" : "Message editor" });
  await user.type(within(editor).getByLabelText(kind === "type" ? "Type name" : "Message name"), name);
  if (id !== undefined) await user.type(within(editor).getByLabelText("Message id"), id);
  for (let index = 1; index < rows.length; index += 1) {
    await user.click(within(editor).getByRole("button", { name: "Add field" }));
  }
  for (const [index, row] of rows.entries()) {
    await fillRow(user, editor, index, row);
  }
  await user.click(within(editor).getByRole("button", { name: `Save ${kind}` }));
  await waitFor(() => {
    expect(within(screen.getByRole("region", { name: kind === "type" ? "Types" : "Messages" })).getByRole("button", { name })).toBeTruthy();
  });
}

describe("struct editor", () => {
  it("creates a nested struct with bits, optional field, dynamic array and CRC", async () => {
    const url = inject("serverUrl");
    const user = userEvent.setup();
    render(<App api={new Api(url)} />);
    await screen.findByRole("option", { name: "demo" });
    await user.selectOptions(screen.getByLabelText("Project"), "demo");

    await user.click(screen.getByRole("button", { name: "New checksum" }));
    const checksums = await screen.findByRole("region", { name: "Checksum editor" });
    await user.type(within(checksums).getByLabelText("Checksum name"), "frameCrc");
    await user.click(within(checksums).getByRole("button", { name: "Save checksum" }));
    await waitFor(() => {
      expect(within(screen.getByRole("region", { name: "Checksums" })).getByRole("button", { name: "frameCrc" })).toBeTruthy();
    });

    await createStruct(user, "type", "Flags", [
      { name: "mode", cells: { bits: "3", max: "5" } },
      { name: "valid", type: "bool", cells: { bits: "1" } },
      { name: "spare", cells: { bits: "4" } },
    ]);
    await createStruct(
      user,
      "message",
      "Packet",
      [
        { name: "flags", type: "Flags" },
        { name: "hasAlt", type: "bool" },
        { name: "alt", type: "i16", occurs: "optional", cells: { count: "hasAlt", min: "-500", max: "9000" } },
        { name: "n", type: "u8", cells: { max: "4" } },
        { name: "samples", type: "u16", occurs: "counted", cells: { capacity: "4", count: "n" } },
        { name: "crc", type: "u32", checksum: "frameCrc", covers: "preceding" },
      ],
      "0x42",
    );

    expect(await screen.findByText("No issues.")).toBeTruthy();
    const preview = await screen.findByRole("region", { name: "C++ preview" });
    await waitFor(() => {
      expect(preview.textContent).toContain("struct Packet {");
    });
    expect(preview.textContent).toContain("wireforge::StaticVector<std::uint16_t, 4U> samples;");

    const project = await new Api(url).project("demo");
    expect(project.ok).toBe(true);
    const packet = await new Api(url).entry("demo", "messages", "Packet");
    expect(packet.data).toMatchObject({
      value: {
        id: 66,
        fields: [
          { name: "flags", type: "Flags" },
          { name: "hasAlt", type: "bool" },
          { name: "alt", type: "i16", min: -500, max: 9000, occurs: { max: 1, count: "hasAlt" } },
          { name: "n", type: "u8", max: 4 },
          { name: "samples", type: "u16", occurs: { max: 4, count: "n" } },
          { name: "crc", type: "u32", checksum: { use: "frameCrc", covers: { scope: "preceding" } } },
        ],
      },
    });
  });

  it("reloads an entry and shows server issues", async () => {
    const url = inject("serverUrl");
    const user = userEvent.setup();
    render(<App api={new Api(url)} />);
    await screen.findByRole("option", { name: "demo" });
    await user.selectOptions(screen.getByLabelText("Project"), "demo");
    const types = await screen.findByRole("region", { name: "Types" });
    await user.click(await within(types).findByRole("button", { name: "Flags" }));
    const editor = await screen.findByRole("region", { name: "Type editor" });
    await waitFor(() => {
      expect(within(editor).getByLabelText<HTMLInputElement>("Field 1 name").value).toBe("mode");
    });
    const bits = within(editor).getByLabelText("Field 3 bits");
    await user.clear(bits);
    await user.type(bits, "5");
    await user.click(within(editor).getByRole("button", { name: "Save type" }));
    const issues = await screen.findByRole("list", { name: "Issues" });
    expect(issues.textContent).toContain("SR-10");
    await user.clear(bits);
    await user.type(bits, "x");
    expect(within(editor).getByRole("list", { name: "Field problems" }).textContent).toContain("bits must be");
    expect(within(editor).getByRole<HTMLButtonElement>("button", { name: "Save type" }).disabled).toBe(true);
  });
});
