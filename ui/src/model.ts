// Conversion between the field table of the editor and the schema types of project files.
import type {
  ChecksumUse,
  Field,
  Framing,
  InterfaceDef,
  InterfaceMessage,
  Occurs,
  ProfileDef,
  StructDef,
} from "./generated/schema";

/** How often a field occurs, as offered by the editor. */
export type OccursKind = "single" | "optional" | "fixed" | "counted" | "fill";

/** One editable row of the field table: every cell is text, as typed by the user. */
export interface FieldRow {
  name: string;
  type: string;
  bits: string;
  occurs: OccursKind;
  capacity: string;
  count: string;
  min: string;
  max: string;
  defaultValue: string;
  condition: string;
  units: string;
  scale: string;
  offset: string;
  checksumUse: string;
  checksumScope: string;
  role: string;
  constValue: string;
  reserved: boolean;
  description: string;
}

/** Checksum scopes of the schema. */
export const CHECKSUM_SCOPES = ["preceding", "payload", "header_and_payload"] as const;

/** Builtin field types of the schema. */
export const BUILTIN_TYPES = [
  "bool",
  "u8",
  "u16",
  "u32",
  "u64",
  "i8",
  "i16",
  "i32",
  "i64",
  "f32",
  "f64",
  "char",
] as const;

/** An empty row for a new field. */
export function emptyRow(name = ""): FieldRow {
  return {
    name,
    type: "u8",
    bits: "",
    occurs: "single",
    capacity: "",
    count: "",
    min: "",
    max: "",
    defaultValue: "",
    condition: "",
    units: "",
    scale: "",
    offset: "",
    checksumUse: "",
    checksumScope: "preceding",
    role: "",
    constValue: "",
    reserved: false,
    description: "",
  };
}

const NUMBER = /^[-+]?(0x[0-9a-fA-F]+|\d+(\.\d+)?([eE][-+]?\d+)?)$/;

/** A number when the text is numeric, otherwise the text itself (an expression or a name). */
export function numberOrExpression(text: string): number | string {
  const trimmed = text.trim();
  if (!NUMBER.test(trimmed)) {
    return trimmed;
  }
  return trimmed.toLowerCase().includes("0x") ? Number.parseInt(trimmed, 16) : Number(trimmed);
}

function scalar(text: string): number | boolean | string {
  const trimmed = text.trim();
  if (trimmed === "true" || trimmed === "false") {
    return trimmed === "true";
  }
  return numberOrExpression(trimmed);
}

function occursOf(row: FieldRow): Occurs | undefined {
  const count = row.count.trim();
  switch (row.occurs) {
    case "single":
      return undefined;
    case "optional":
      return count === "" ? { max: 1 } : { max: 1, count: numberOrExpression(count) };
    case "fixed":
      return { max: numberOrExpression(row.capacity) };
    case "counted":
      return { max: numberOrExpression(row.capacity), count: numberOrExpression(count) };
    case "fill":
      return { max: numberOrExpression(row.capacity), fill: true };
  }
}

function checksumOf(row: FieldRow): ChecksumUse | undefined {
  if (row.checksumUse.trim() === "") {
    return undefined;
  }
  return { use: row.checksumUse.trim(), covers: { scope: row.checksumScope } } as ChecksumUse;
}

/** The schema field of a row; empty cells are left out. */
export function toField(row: FieldRow): Field {
  const field: Field = { name: row.name.trim(), type: row.type.trim() };
  if (row.bits.trim() !== "") field.bits = Number(row.bits);
  if (row.min.trim() !== "") field.min = numberOrExpression(row.min);
  if (row.max.trim() !== "") field.max = numberOrExpression(row.max);
  if (row.defaultValue.trim() !== "") field.default = scalar(row.defaultValue);
  if (row.units.trim() !== "") field.units = row.units.trim();
  if (row.scale.trim() !== "") field.scale = Number(row.scale);
  if (row.offset.trim() !== "") field.offset = Number(row.offset);
  const occurs = occursOf(row);
  if (occurs !== undefined) field.occurs = occurs;
  const checksum = checksumOf(row);
  if (checksum !== undefined) field.checksum = checksum;
  if (row.constValue.trim() !== "") field.const = scalar(row.constValue);
  if (row.reserved) field.reserved = true;
  if (row.role !== "") field.role = row.role as NonNullable<Field["role"]>;
  if (row.condition.trim() !== "") field.if = row.condition.trim();
  if (row.description.trim() !== "") field.description = row.description.trim();
  return field;
}

function text(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function occursKind(occurs: Occurs | undefined): OccursKind {
  if (occurs === undefined) return "single";
  if (occurs.fill === true) return "fill";
  if (occurs.count === undefined) return occurs.max === 1 ? "optional" : "fixed";
  return occurs.max === 1 ? "optional" : "counted";
}

/** The editor row of a schema field. */
export function fromField(field: Field): FieldRow {
  const covers = field.checksum?.covers as { scope?: string } | undefined;
  return {
    ...emptyRow(field.name),
    type: field.type,
    bits: text(field.bits),
    occurs: occursKind(field.occurs),
    capacity: text(field.occurs?.max),
    count: text(field.occurs?.count),
    min: text(field.min),
    max: text(field.max),
    defaultValue: text(field.default),
    condition: text(field.if),
    units: text(field.units),
    scale: text(field.scale),
    offset: text(field.offset),
    checksumUse: text(field.checksum?.use),
    checksumScope: covers?.scope ?? "preceding",
    role: field.role ?? "",
    constValue: text(field.const),
    reserved: field.reserved === true,
    description: text(field.description),
  };
}

/** Problems of the rows that the server would reject anyway, shown while typing. */
export function rowProblems(rows: readonly FieldRow[]): string[] {
  const problems: string[] = [];
  const names = new Set<string>();
  rows.forEach((row, index) => {
    const label = row.name.trim() === "" ? `row ${String(index + 1)}` : row.name.trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(row.name.trim())) {
      problems.push(`${label}: the name must be an identifier`);
    }
    if (names.has(row.name.trim())) {
      problems.push(`${label}: duplicate name`);
    }
    names.add(row.name.trim());
    if (row.bits.trim() !== "" && !/^\d+$/.test(row.bits.trim())) {
      problems.push(`${label}: bits must be a positive integer`);
    }
    if (row.scale.trim() !== "" && !(Number.isFinite(Number(row.scale)) && Number(row.scale) !== 0)) {
      problems.push(`${label}: scale must be a number other than 0`);
    }
    if (row.offset.trim() !== "" && !Number.isFinite(Number(row.offset))) {
      problems.push(`${label}: offset must be a number`);
    }
    if (["fixed", "counted", "fill"].includes(row.occurs) && row.capacity.trim() === "") {
      problems.push(`${label}: the array needs a capacity`);
    }
    if (row.occurs === "counted" && row.count.trim() === "") {
      problems.push(`${label}: a counted array needs a count field or expression`);
    }
  });
  return problems;
}

/** A struct definition (type or message body) from rows and an optional description. */
export function toStruct(rows: readonly FieldRow[], description: string): StructDef {
  const fields = rows.map(toField);
  const struct: StructDef = { fields: fields as StructDef["fields"] };
  if (description.trim() !== "") struct.description = description.trim();
  return struct;
}

/** Header field roles of the schema. */
export const ROLES = ["", "sync", "message_id", "payload_length", "protocol_version", "sequence"] as const;

/** One message of an interface as edited: name and optional id override. */
export interface InterfaceMessageRow {
  name: string;
  id: string;
}

/** The editable form of an interface. */
export interface InterfaceForm {
  version: string;
  description: string;
  direction: InterfaceDef["direction"];
  endian: "" | "big" | "little";
  rawHeader: boolean;
  header: FieldRow[];
  trailer: FieldRow[];
  framing: Framing["kind"];
  frameSize: string;
  maxFrameSize: string;
  lengthCounts: "" | NonNullable<Framing["length_counts"]>;
  resync: boolean;
  messages: InterfaceMessageRow[];
  rxBytes: string;
  txBytes: string;
  allowTrailingBytes: boolean;
}

/** A new interface: tx, no header, no framing. */
export function emptyInterface(): InterfaceForm {
  return {
    version: "1.0.0",
    description: "",
    direction: "tx",
    endian: "",
    rawHeader: true,
    header: [],
    trailer: [],
    framing: "none",
    frameSize: "",
    maxFrameSize: "",
    lengthCounts: "",
    resync: false,
    messages: [],
    rxBytes: "",
    txBytes: "",
    allowTrailingBytes: false,
  };
}

function framingOf(form: InterfaceForm): Framing {
  const framing: Framing = { kind: form.framing };
  if (form.frameSize.trim() !== "") framing.frame_size = numberOrExpression(form.frameSize);
  if (form.maxFrameSize.trim() !== "") framing.max_frame_size = numberOrExpression(form.maxFrameSize);
  if (form.lengthCounts !== "") framing.length_counts = form.lengthCounts;
  if (form.resync) framing.resync = true;
  return framing;
}

function messageOf(row: InterfaceMessageRow): InterfaceMessage {
  return row.id.trim() === ""
    ? row.name.trim()
    : { name: row.name.trim(), id: Number(numberOrExpression(row.id)) };
}

/** The schema interface of a form; empty optional parts are left out. */
export function toInterface(form: InterfaceForm): InterfaceDef {
  const result: InterfaceDef = {
    version: form.version.trim(),
    direction: form.direction,
    messages: form.messages.filter((row) => row.name.trim() !== "").map(messageOf),
  };
  if (form.description.trim() !== "") result.description = form.description.trim();
  if (form.endian !== "") result.endian = form.endian;
  result.header = form.rawHeader
    ? "none"
    : { fields: form.header.map(toField) as [Field, ...Field[]] };
  if (form.trailer.length > 0) {
    result.trailer = { fields: form.trailer.map(toField) as [Field, ...Field[]] };
  }
  result.framing = framingOf(form);
  const buffers: NonNullable<InterfaceDef["buffers"]> = {};
  if (form.rxBytes.trim() !== "") buffers.rx_bytes = numberOrExpression(form.rxBytes);
  if (form.txBytes.trim() !== "") buffers.tx_bytes = numberOrExpression(form.txBytes);
  if (Object.keys(buffers).length > 0) result.buffers = buffers;
  if (form.allowTrailingBytes) result.allow_trailing_bytes = true;
  return result;
}

/** The form of a schema interface. */
export function fromInterface(value: InterfaceDef): InterfaceForm {
  const header = value.header;
  const framing = value.framing;
  return {
    version: value.version,
    description: value.description ?? "",
    direction: value.direction,
    endian: value.endian ?? "",
    rawHeader: header === undefined || header === "none",
    header: typeof header === "object" ? header.fields.map(fromField) : [],
    trailer: value.trailer?.fields.map(fromField) ?? [],
    framing: framing?.kind ?? "none",
    frameSize: text(framing?.frame_size),
    maxFrameSize: text(framing?.max_frame_size),
    lengthCounts: framing?.length_counts ?? "",
    resync: framing?.resync === true,
    messages: value.messages.map((entry) =>
      typeof entry === "string"
        ? { name: entry, id: "" }
        : { name: entry.name, id: entry.id === undefined ? "" : `0x${entry.id.toString(16)}` },
    ),
    rxBytes: text(value.buffers?.rx_bytes),
    txBytes: text(value.buffers?.tx_bytes),
    allowTrailingBytes: value.allow_trailing_bytes === true,
  };
}

/** One define of a profile as edited. */
export interface DefineRow {
  name: string;
  value: string;
}

/** The schema profile of a description and define rows. */
export function toProfile(description: string, rows: readonly DefineRow[]): ProfileDef {
  const defines: ProfileDef["defines"] = {};
  for (const row of rows) {
    if (row.name.trim() !== "") defines[row.name.trim()] = Number(numberOrExpression(row.value));
  }
  const profile: ProfileDef = { defines };
  if (description.trim() !== "") profile.description = description.trim();
  return profile;
}

/** The define rows of a schema profile. */
export function fromProfile(value: ProfileDef): DefineRow[] {
  return Object.entries(value.defines).map(([name, number]) => ({ name, value: String(number) }));
}

// --- keeping what the editors do not show -----------------------------------------------------

/** Keys a type editor writes. */
export const TYPE_KEYS = ["description", "fields"] as const;
/** Keys a message editor writes. */
export const MESSAGE_KEYS = ["description", "id", "fields", "endian", "bit_order", "range_check"] as const;
/** Keys the interface editor writes. */
export const INTERFACE_KEYS = [
  "version",
  "description",
  "direction",
  "endian",
  "header",
  "trailer",
  "framing",
  "messages",
  "buffers",
  "allow_trailing_bytes",
] as const;

/**
 * The definition to save: `original` (as loaded) with the `managed` keys replaced by `edited`.
 * Keys the editor does not show (conditions, versions, deprecation, ...) are kept as they are.
 */
export function withEdits(
  original: Readonly<Record<string, unknown>>,
  edited: Readonly<Record<string, unknown>>,
  managed: readonly string[],
): Record<string, unknown> {
  const result: Record<string, unknown> = { ...original };
  for (const key of managed) {
    if (key in edited && edited[key] !== undefined) {
      result[key] = edited[key];
    } else {
      Reflect.deleteProperty(result, key);
    }
  }
  return result;
}

// --- message settings -------------------------------------------------------------------------

/** A range check direction: project default, on or off. */
export type Toggle = "" | "on" | "off";

/** Byte order, bit order and range checks of a message (empty: project default). */
export interface MessageSettings {
  endian: "" | "big" | "little";
  bitOrder: "" | "msb_first" | "lsb_first";
  encodeCheck: Toggle;
  decodeCheck: Toggle;
}

/** Settings that all follow the project defaults. */
export function defaultSettings(): MessageSettings {
  return { endian: "", bitOrder: "", encodeCheck: "", decodeCheck: "" };
}

function toggle(value: unknown): Toggle {
  if (value === true) return "on";
  if (value === false) return "off";
  return "";
}

/** The settings of a loaded message or project defaults (`endian`, `bit_order`, `range_check`). */
export function fromSettings(value: Readonly<Record<string, unknown>>): MessageSettings {
  const checks = (value.range_check ?? {}) as { encode?: unknown; decode?: unknown };
  const endian = value.endian === "big" || value.endian === "little" ? value.endian : "";
  const order = value.bit_order === "msb_first" || value.bit_order === "lsb_first" ? value.bit_order : "";
  return { endian, bitOrder: order, encodeCheck: toggle(checks.encode), decodeCheck: toggle(checks.decode) };
}

/** The `endian`, `bit_order` and `range_check` keys of settings (absent when default). */
export function toSettings(settings: MessageSettings): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  if (settings.endian !== "") result.endian = settings.endian;
  if (settings.bitOrder !== "") result.bit_order = settings.bitOrder;
  const checks: Record<string, boolean> = {};
  if (settings.encodeCheck !== "") checks.encode = settings.encodeCheck === "on";
  if (settings.decodeCheck !== "") checks.decode = settings.decodeCheck === "on";
  if (Object.keys(checks).length > 0) result.range_check = checks;
  return result;
}

// --- enums ------------------------------------------------------------------------------------

/** Underlying types of enums. */
export const ENUM_TYPES = ["u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64"] as const;

/** One enum member as edited. */
export interface EnumValueRow {
  name: string;
  value: string;
}

/** The editable form of an enum. */
export interface EnumForm {
  type: string;
  description: string;
  values: EnumValueRow[];
}

/** The schema enum of a form. */
export function toEnum(form: EnumForm): Record<string, unknown> {
  const values: Record<string, number> = {};
  for (const row of form.values) {
    if (row.name.trim() !== "") values[row.name.trim()] = Number(numberOrExpression(row.value));
  }
  const result: Record<string, unknown> = { type: form.type, values };
  if (form.description.trim() !== "") result.description = form.description.trim();
  return result;
}

/** The form of a loaded enum. */
export function fromEnum(value: Readonly<Record<string, unknown>>): EnumForm {
  const values = (value.values ?? {}) as Record<string, number>;
  return {
    type: typeof value.type === "string" ? value.type : "u8",
    description: typeof value.description === "string" ? value.description : "",
    values: Object.entries(values).map(([name, number]) => ({ name, value: String(number) })),
  };
}

/** Problems of enum members the server would reject anyway. */
export function enumProblems(form: EnumForm): string[] {
  const problems: string[] = [];
  const names = new Set<string>();
  const numbers = new Set<number>();
  for (const row of form.values) {
    const name = row.name.trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) problems.push(`${name || "member"}: the name must be an identifier`);
    if (names.has(name)) problems.push(`${name}: duplicate name`);
    names.add(name);
    const number = numberOrExpression(row.value);
    if (typeof number !== "number" || !Number.isInteger(number)) {
      problems.push(`${name}: the value must be an integer`);
    } else if (numbers.has(number)) {
      problems.push(`${name}: duplicate value ${String(number)}`);
    } else {
      numbers.add(number);
    }
  }
  if (form.values.length === 0) problems.push("the enum needs a member");
  return problems;
}

// --- project settings -------------------------------------------------------------------------

/** Root keys the project settings editor writes, in file order. */
export const PROJECT_KEYS = ["project", "version", "description", "namespace", "defaults", "defines"] as const;

/** The editable project settings. */
export interface ProjectForm {
  name: string;
  version: string;
  description: string;
  namespace: string;
  defaults: MessageSettings;
  defines: DefineRow[];
}

/** The settings form of a loaded root document. */
export function fromProject(value: Readonly<Record<string, unknown>>): ProjectForm {
  const defines = (value.defines ?? {}) as Record<string, number>;
  const text = (key: string) => {
    const item = value[key];
    return typeof item === "string" ? item : "";
  };
  return {
    name: text("project"),
    version: text("version"),
    description: text("description"),
    namespace: text("namespace"),
    defaults: fromSettings((value.defaults ?? {}) as Record<string, unknown>),
    defines: Object.entries(defines).map(([name, number]) => ({ name, value: String(number) })),
  };
}

/** The root keys of the settings; a key mapped to undefined is removed from the file. */
export function toProject(form: ProjectForm): Record<(typeof PROJECT_KEYS)[number], unknown> {
  const defines: Record<string, number> = {};
  for (const row of form.defines) {
    if (row.name.trim() !== "") defines[row.name.trim()] = Number(numberOrExpression(row.value));
  }
  const defaults = toSettings(form.defaults);
  const optional = (text: string) => (text.trim() === "" ? undefined : text.trim());
  return {
    project: form.name.trim(),
    version: optional(form.version),
    description: optional(form.description),
    namespace: optional(form.namespace),
    defaults: Object.keys(defaults).length > 0 ? defaults : undefined,
    defines: Object.keys(defines).length > 0 ? defines : undefined,
  };
}
