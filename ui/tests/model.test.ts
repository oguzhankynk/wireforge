import { describe, expect, it } from "vitest";
import type { Field } from "../src/generated/schema";
import {
  defaultSettings,
  emptyInterface,
  enumProblems,
  fromEnum,
  fromProject,
  fromSettings,
  MESSAGE_KEYS,
  toEnum,
  toProject,
  toSettings,
  withEdits,
  emptyRow,
  fromField,
  fromInterface,
  fromProfile,
  numberOrExpression,
  rowProblems,
  toField,
  toInterface,
  toProfile,
  toStruct,
} from "../src/model";

describe("field rows", () => {
  it("parses numbers and keeps expressions", () => {
    expect(numberOrExpression(" 12 ")).toBe(12);
    expect(numberOrExpression("0x1F")).toBe(31);
    expect(numberOrExpression("-2.5")).toBe(-2.5);
    expect(numberOrExpression("MAX_TRACKS * 2")).toBe("MAX_TRACKS * 2");
  });

  it("converts every occurrence kind", () => {
    const base = emptyRow("a");
    expect(toField(base)).toEqual({ name: "a", type: "u8" });
    expect(toField({ ...base, occurs: "optional", count: "hasA" }).occurs).toEqual({ max: 1, count: "hasA" });
    expect(toField({ ...base, occurs: "optional" }).occurs).toEqual({ max: 1 });
    expect(toField({ ...base, occurs: "fixed", capacity: "CHANNELS" }).occurs).toEqual({ max: "CHANNELS" });
    expect(toField({ ...base, occurs: "counted", capacity: "4", count: "n" }).occurs).toEqual({ max: 4, count: "n" });
    expect(toField({ ...base, occurs: "fill", capacity: "16" }).occurs).toEqual({ max: 16, fill: true });
  });

  it("round-trips fields", () => {
    const field = {
      name: "crc",
      type: "u32",
      bits: 7,
      min: -1,
      max: "LIMIT",
      default: true,
      units: "m",
      scale: 0.01,
      offset: -40,
      if: "defined(X)",
      description: "d",
      checksum: { use: "frameCrc", covers: { scope: "payload" as const } },
    };
    expect(toField(fromField(field))).toEqual(field);
    expect(fromField({ name: "n", type: "u8", occurs: { max: 3, count: "c" } }).occurs).toBe("counted");
    expect(fromField({ name: "n", type: "u8", occurs: { max: 1, count: "c" } }).occurs).toBe("optional");
    expect(fromField({ name: "n", type: "u8", occurs: { max: 3 } }).occurs).toBe("fixed");
    expect(fromField({ name: "n", type: "u8", occurs: { max: 3, fill: true } }).occurs).toBe("fill");
  });

  it("reports scale and offset that are not numbers", () => {
    const rows = [
      { ...emptyRow("a"), scale: "0" },
      { ...emptyRow("b"), scale: "x", offset: "y" },
      { ...emptyRow("c"), scale: "0.5", offset: "-3" },
    ];
    expect(rowProblems(rows)).toEqual([
      "a: scale must be a number other than 0",
      "b: scale must be a number other than 0",
      "b: offset must be a number",
    ]);
  });

  it("reports row problems", () => {
    const rows = [
      { ...emptyRow("1bad"), bits: "x" },
      emptyRow("dup"),
      { ...emptyRow("dup"), occurs: "counted" as const },
    ];
    expect(rowProblems(rows)).toEqual([
      "1bad: the name must be an identifier",
      "1bad: bits must be a positive integer",
      "dup: duplicate name",
      "dup: the array needs a capacity",
      "dup: a counted array needs a count field or expression",
    ]);
    expect(toStruct([emptyRow("a")], " Some type. ")).toEqual({
      description: "Some type.",
      fields: [{ name: "a", type: "u8" }],
    });
  });
});

describe("interfaces and profiles", () => {
  it("round-trips an interface", () => {
    const value = {
      version: "1.2.0",
      description: "To node A.",
      direction: "tx" as const,
      endian: "big" as const,
      header: {
        fields: [
          { name: "sync", type: "u16", const: 42330, role: "sync" as const },
          { name: "length", type: "u16", role: "payload_length" as const },
        ] as [Field, ...Field[]],
      },
      trailer: { fields: [{ name: "crc", type: "u16", checksum: { use: "c", covers: { scope: "header_and_payload" as const } } }] as [Field] },
      framing: { kind: "sync_length" as const, max_frame_size: "APP_MAX_FRAME", length_counts: "payload" as const, resync: true },
      messages: ["NavSolution", { name: "Command", id: 48 }],
      buffers: { rx_bytes: 256, tx_bytes: "MAX" },
      allow_trailing_bytes: true,
    };
    const form = fromInterface(value);
    expect(form.messages[1]).toEqual({ name: "Command", id: "0x30" });
    expect(toInterface(form)).toEqual(value);
  });

  it("writes a raw interface without optional parts", () => {
    const form = { ...emptyInterface(), messages: [{ name: "M", id: "" }, { name: " ", id: "" }] };
    expect(toInterface(form)).toEqual({
      version: "1.0.0",
      direction: "tx",
      header: "none",
      framing: { kind: "none" },
      messages: ["M"],
    });
    expect(fromInterface({ version: "1.0.0", direction: "rx", messages: ["M"] }).rawHeader).toBe(true);
  });

  it("round-trips a profile", () => {
    const rows = fromProfile({ description: "Big", defines: { A: 1, MAX: 5 } });
    expect(rows).toEqual([
      { name: "A", value: "1" },
      { name: "MAX", value: "5" },
    ]);
    expect(toProfile("", [...rows, { name: "", value: "9" }])).toEqual({ defines: { A: 1, MAX: 5 } });
  });
});

describe("editing keeps what the editor does not show", () => {
  it("replaces only the managed keys", () => {
    const original = { id: 1, description: "Old.", fields: [], bit_order: "lsb_first", if: "defined(X)", since: "1.0.0" };
    const edited = { id: 2, fields: [{ name: "a", type: "u8" }], endian: "big" };
    expect(withEdits(original, edited, MESSAGE_KEYS)).toEqual({
      id: 2,
      fields: [{ name: "a", type: "u8" }],
      if: "defined(X)",
      since: "1.0.0",
      endian: "big",
    });
  });
});

describe("message settings", () => {
  it("round-trips byte order, bit order and range checks", () => {
    expect(toSettings(defaultSettings())).toEqual({});
    const value = { endian: "little", bit_order: "lsb_first", range_check: { encode: true, decode: false } };
    const settings = fromSettings(value);
    expect(settings).toEqual({ endian: "little", bitOrder: "lsb_first", encodeCheck: "on", decodeCheck: "off" });
    expect(toSettings(settings)).toEqual(value);
    expect(toSettings({ ...defaultSettings(), decodeCheck: "off" })).toEqual({ range_check: { decode: false } });
    expect(fromSettings({ endian: "middle" }).endian).toBe("");
  });
});

describe("enums", () => {
  it("round-trips an enum", () => {
    const value = { type: "u16", description: "Mode.", values: { IDLE: 0, RUN: 1, FAULT: 0x10 } };
    const form = fromEnum(value);
    expect(form.values[2]).toEqual({ name: "FAULT", value: "16" });
    expect(toEnum(form)).toEqual(value);
    expect(enumProblems(form)).toEqual([]);
    expect(fromEnum({}).type).toBe("u8");
  });

  it("reports member problems", () => {
    const form = {
      type: "u8",
      description: "",
      values: [
        { name: "A", value: "0" },
        { name: "A", value: "0" },
        { name: "1x", value: "2.5" },
      ],
    };
    expect(enumProblems(form)).toEqual([
      "A: duplicate name",
      "A: duplicate value 0",
      "1x: the name must be an identifier",
      "1x: the value must be an integer",
    ]);
    expect(enumProblems({ type: "u8", description: "", values: [] })).toEqual(["the enum needs a member"]);
  });
});

describe("project settings", () => {
  it("round-trips the root keys and drops cleared ones", () => {
    const root = {
      schema_version: 1,
      project: "demo",
      version: "0.1.0",
      namespace: "demo::link",
      defaults: { endian: "big" },
      defines: { MAX: 8 },
    };
    const form = fromProject(root);
    expect(form.defaults.endian).toBe("big");
    expect(toProject(form)).toEqual({
      project: "demo",
      version: "0.1.0",
      description: undefined,
      namespace: "demo::link",
      defaults: { endian: "big" },
      defines: { MAX: 8 },
    });
    const cleared = toProject({ ...form, namespace: " ", defaults: defaultSettings(), defines: [{ name: "", value: "1" }] });
    expect(cleared.namespace).toBeUndefined();
    expect(cleared.defaults).toBeUndefined();
    expect(cleared.defines).toBeUndefined();
  });
});
