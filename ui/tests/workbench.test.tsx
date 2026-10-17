// Acceptance: the three-interface example is edited end to end in the UI (interfaces, profiles,
// a new interface, playground, layout, versions) against a real server; files keep minimal diffs.
import { execFileSync } from "node:child_process";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { afterEach, describe, expect, inject, it } from "vitest";
import { Api } from "../src/api";
import { App } from "../src/App";

afterEach(() => {
  cleanup();
});

function changedLines(): string[] {
  const diff = execFileSync("git", ["-C", inject("workspace"), "diff", "-U0", "--", "avionics"]).toString();
  return diff.split("\n").filter((line) => /^[-+][^-+]/.test(line));
}

async function open(user: UserEvent, section: string, name: string) {
  const list = screen.getByRole("region", { name: section });
  await user.click(await within(list).findByRole("button", { name }));
}

async function saveAndExpectNoIssues(user: UserEvent, editor: HTMLElement, button: string, entry: string) {
  await user.click(within(editor).getByRole("button", { name: button }));
  await waitFor(() => {
    expect(screen.getByLabelText("Save result").textContent).toBe(`Saved ${entry}.`);
  });
  await waitFor(() => {
    expect(screen.getByText("No issues.")).toBeTruthy();
  });
}

describe("three-interface example", () => {
  it("is editable end to end", async () => {
    const url = inject("serverUrl");
    const user = userEvent.setup();
    render(<App api={new Api(url)} />);
    await screen.findByRole("option", { name: "avionics" });
    await user.selectOptions(screen.getByLabelText("Project"), "avionics");
    const interfaces = screen.getByRole("region", { name: "Interfaces" });
    for (const name of ["nodeB_to_our", "our_to_nodeA", "our_to_nodeC"]) {
      expect(await within(interfaces).findByRole("button", { name })).toBeTruthy();
    }

    // Interface: turn off resynchronization and describe the header length field.
    await open(user, "Interfaces", "our_to_nodeA");
    let editor = await screen.findByRole("region", { name: "Interface editor" });
    await waitFor(() => {
      expect(within(editor).getByLabelText<HTMLInputElement>("Header field 4 name").value).toBe("length");
    });
    await user.click(within(editor).getByLabelText("Resync"));
    await user.type(within(editor).getByLabelText("Header field 4 description"), "Payload bytes.");
    await saveAndExpectNoIssues(user, editor, "Save interface", "interfaces/our_to_nodeA");

    // Profile: platformB tracks 6 targets.
    await open(user, "Profiles", "platformB");
    editor = await screen.findByRole("region", { name: "Profile editor" });
    await waitFor(() => {
      expect(within(editor).getByLabelText<HTMLInputElement>("Define 2 value").value).toBe("5");
    });
    await user.clear(within(editor).getByLabelText("Define 2 value"));
    await user.type(within(editor).getByLabelText("Define 2 value"), "6");
    await saveAndExpectNoIssues(user, editor, "Save profile", "profiles/platformB");

    // A fourth interface, length-prefixed, carrying Command with its own id.
    await user.click(within(interfaces).getByRole("button", { name: "New interface" }));
    editor = await screen.findByRole("region", { name: "Interface editor" });
    await user.type(within(editor).getByLabelText("Interface name"), "our_to_nodeD");
    await user.click(within(editor).getByLabelText(/No header/));
    for (let index = 0; index < 2; index += 1) {
      await user.click(within(editor).getByRole("button", { name: "Add header field" }));
    }
    await user.clear(within(editor).getByLabelText("Header field 1 name"));
    await user.type(within(editor).getByLabelText("Header field 1 name"), "msgId");
    await user.selectOptions(within(editor).getByLabelText("Header field 1 type"), "u16");
    await user.selectOptions(within(editor).getByLabelText("Header field 1 role"), "message_id");
    await user.clear(within(editor).getByLabelText("Header field 2 name"));
    await user.type(within(editor).getByLabelText("Header field 2 name"), "length");
    await user.selectOptions(within(editor).getByLabelText("Header field 2 role"), "payload_length");
    await user.selectOptions(within(editor).getByLabelText("Framing kind"), "length_prefixed");
    await user.type(within(editor).getByLabelText("Max frame size"), "64");
    await user.click(within(editor).getByRole("button", { name: "Add message" }));
    await user.selectOptions(within(editor).getByLabelText("Message 1 name"), "Command");
    await user.type(within(editor).getByLabelText("Message 1 id"), "0x40");
    await saveAndExpectNoIssues(user, editor, "Save interface", "interfaces/our_to_nodeD");
    expect(await within(interfaces).findByRole("button", { name: "our_to_nodeD" })).toBeTruthy();

    // Playground: encode a Command frame on the new interface and decode it again.
    await user.click(screen.getByRole("tab", { name: "Playground" }));
    const playground = await screen.findByRole("region", { name: "Playground" });
    await user.selectOptions(within(playground).getByLabelText("Playground message"), "Command");
    await user.selectOptions(within(playground).getByLabelText("Playground interface"), "our_to_nodeD");
    await user.selectOptions(within(playground).getByLabelText("Playground profile"), "platformA");
    const value = within(playground).getByLabelText("Value (JSON)");
    await user.clear(value);
    await user.click(value);
    await user.paste('{"target": 2, "opcode": 7}');
    await user.click(within(playground).getByRole("button", { name: "Encode" }));
    const output = within(playground).getByLabelText("Playground output");
    await waitFor(() => {
      expect(output.textContent).toContain("frame   004005");
    });
    await user.click(within(playground).getByRole("button", { name: "Decode" }));
    await waitFor(() => {
      expect(output.textContent).toContain('"opcode": 7');
    });

    // Layout: bit positions of the lsb_first bit group.
    await user.click(screen.getByRole("tab", { name: "Layout" }));
    const layout = await screen.findByRole("region", { name: "Layout" });
    await within(layout).findByRole("option", { name: "NavSolution" });
    await user.selectOptions(within(layout).getByLabelText("Layout message"), "NavSolution");
    const table = within(layout).getByRole("table", { name: "Message layout" });
    expect(within(table).getByText("flags.mode").closest("tr")?.textContent).toContain("3..1");
    expect(within(layout).getByLabelText("Link RAM").textContent).toContain("our_to_nodeD");

    // Versions: the working copy against the committed baseline.
    await user.click(screen.getByRole("tab", { name: "Versions" }));
    const versions = await screen.findByRole("region", { name: "Versions" });
    await user.click(await within(versions).findByRole("button", { name: /^Compare with/ }));
    const report = await within(versions).findByLabelText("Compatibility report");
    expect(report.textContent).toContain("interface our_to_nodeA");
    expect(report.textContent).toContain("interface our_to_nodeD");

    // Files: only the edited lines changed (plus the new interface).
    const project = await new Api(url).project("avionics");
    expect(project.ok).toBe(true);
    expect(changedLines()).toEqual([
      "-        - { name: length, type: u16, role: payload_length }",
      "+        - { name: length, type: u16, role: payload_length, description: Payload bytes. }",
      "-    framing: { kind: sync_length, max_frame_size: APP_MAX_FRAME, resync: true }",
      "+    framing: { kind: sync_length, max_frame_size: APP_MAX_FRAME }",
      "-    defines: { PLATFORM_B: 1, MAX_TRACKS: 5, CHANNELS: 5 }",
      "+    defines: { PLATFORM_B: 1, MAX_TRACKS: 6, CHANNELS: 5 }",
    ]);
  });
});
