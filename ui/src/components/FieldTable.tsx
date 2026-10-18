import { CHECKSUM_SCOPES, emptyRow, ROLES, type FieldRow, type OccursKind } from "../model";

const OCCURS: readonly [OccursKind, string][] = [
  ["single", "single"],
  ["optional", "optional (0..1)"],
  ["fixed", "fixed array"],
  ["counted", "counted array"],
  ["fill", "fill payload"],
];

type TextColumn = "bits" | "capacity" | "count" | "min" | "max" | "defaultValue" | "units" | "scale" | "offset" | "condition" | "description" | "constValue";

const BODY_COLUMNS: readonly [TextColumn, string][] = [
  ["bits", "bits"],
  ["capacity", "capacity"],
  ["count", "count"],
  ["min", "min"],
  ["max", "max"],
  ["defaultValue", "default"],
  ["units", "units"],
  ["scale", "scale"],
  ["offset", "offset"],
  ["condition", "if"],
  ["description", "description"],
];
const HEADER_COLUMNS: readonly [TextColumn, string][] = [
  ["bits", "bits"],
  ["constValue", "const"],
  ["min", "min"],
  ["max", "max"],
  ["description", "description"],
];

/** Props of the field table. */
export interface FieldTableProps {
  /** Names the rows in labels ("Field", "Header field", ...). */
  label: string;
  rows: readonly FieldRow[];
  onChange: (rows: FieldRow[]) => void;
  typeOptions: readonly string[];
  checksumOptions: readonly string[];
  /** Header/trailer layout: role, const and reserved instead of occurrences and ranges. */
  frame?: boolean;
}

/** Editable table of fields: one row per field, cells as text. */
export function FieldTable(props: FieldTableProps) {
  const { label, rows, onChange, typeOptions, checksumOptions, frame = false } = props;
  const columns = frame ? HEADER_COLUMNS : BODY_COLUMNS;
  const update = (index: number, change: Partial<FieldRow>) => {
    onChange(rows.map((row, at) => (at === index ? { ...row, ...change } : row)));
  };
  const move = (index: number, step: number) => {
    const target = index + step;
    if (target < 0 || target >= rows.length) return;
    const next = [...rows];
    const [row] = next.splice(index, 1);
    if (row !== undefined) next.splice(target, 0, row);
    onChange(next);
  };

  return (
    <>
      <table aria-label={`${label}s`}>
        <thead>
          <tr>
            <th>name</th>
            <th>type</th>
            {frame ? <th>role</th> : <th>occurs</th>}
            {columns.map(([, text]) => (
              <th key={text}>{text}</th>
            ))}
            {frame && <th>reserved</th>}
            <th>checksum</th>
            <th>covers</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const name = `${label} ${String(index + 1)}`;
            return (
              <tr key={index}>
                <td>
                  <input
                    aria-label={`${name} name`}
                    value={row.name}
                    onChange={(event) => { update(index, { name: event.target.value }); }}
                  />
                </td>
                <td>
                  <select
                    aria-label={`${name} type`}
                    value={row.type}
                    onChange={(event) => { update(index, { type: event.target.value }); }}
                  >
                    {typeOptions.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  {frame ? (
                    <select
                      aria-label={`${name} role`}
                      value={row.role}
                      onChange={(event) => { update(index, { role: event.target.value }); }}
                    >
                      {ROLES.map((role) => (
                        <option key={role} value={role}>
                          {role === "" ? "-" : role}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <select
                      aria-label={`${name} occurs`}
                      value={row.occurs}
                      onChange={(event) => { update(index, { occurs: event.target.value as OccursKind }); }}
                    >
                      {OCCURS.map(([value, text]) => (
                        <option key={value} value={value}>
                          {text}
                        </option>
                      ))}
                    </select>
                  )}
                </td>
                {columns.map(([column, text]) => (
                  <td key={column}>
                    <input
                      aria-label={`${name} ${text}`}
                      value={row[column]}
                      onChange={(event) => { update(index, { [column]: event.target.value }); }}
                    />
                  </td>
                ))}
                {frame && (
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`${name} reserved`}
                      checked={row.reserved}
                      onChange={(event) => { update(index, { reserved: event.target.checked }); }}
                    />
                  </td>
                )}
                <td>
                  <select
                    aria-label={`${name} checksum`}
                    value={row.checksumUse}
                    onChange={(event) => { update(index, { checksumUse: event.target.value }); }}
                  >
                    <option value="">-</option>
                    {checksumOptions.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <select
                    aria-label={`${name} covers`}
                    value={row.checksumScope}
                    onChange={(event) => { update(index, { checksumScope: event.target.value }); }}
                  >
                    {CHECKSUM_SCOPES.map((scope) => (
                      <option key={scope} value={scope}>
                        {scope}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <button type="button" aria-label={`${name} up`} onClick={() => { move(index, -1); }}>
                    ↑
                  </button>
                  <button type="button" aria-label={`${name} down`} onClick={() => { move(index, 1); }}>
                    ↓
                  </button>
                  <button
                    type="button"
                    aria-label={`${name} remove`}
                    onClick={() => { onChange(rows.filter((_, at) => at !== index)); }}
                  >
                    ✕
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <button
        type="button"
        onClick={() => { onChange([...rows, emptyRow(`field${String(rows.length + 1)}`)]); }}
      >
        Add {label.toLowerCase()}
      </button>
    </>
  );
}
