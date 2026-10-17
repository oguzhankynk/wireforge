import type { MessageSettings, Toggle } from "../model";

const TOGGLES: readonly [Toggle, string][] = [
  ["", "(default)"],
  ["on", "on"],
  ["off", "off"],
];

/** Byte order, bit order and range checks; `inherit` names what an empty choice falls back to. */
export function SettingsFields(props: {
  settings: MessageSettings;
  onChange: (settings: MessageSettings) => void;
  inherit: string;
}) {
  const { settings, onChange, inherit } = props;
  const set = (change: Partial<MessageSettings>) => {
    onChange({ ...settings, ...change });
  };
  return (
    <fieldset>
      <legend>Encoding</legend>
      <label>
        Byte order
        <select
          value={settings.endian}
          onChange={(event) => { set({ endian: event.target.value as MessageSettings["endian"] }); }}
        >
          <option value="">{inherit}</option>
          <option value="big">big</option>
          <option value="little">little</option>
        </select>
      </label>
      <label>
        Bit order
        <select
          value={settings.bitOrder}
          onChange={(event) => { set({ bitOrder: event.target.value as MessageSettings["bitOrder"] }); }}
        >
          <option value="">{inherit}</option>
          <option value="msb_first">msb_first</option>
          <option value="lsb_first">lsb_first</option>
        </select>
      </label>
      <label>
        Range check on encode
        <select
          value={settings.encodeCheck}
          onChange={(event) => { set({ encodeCheck: event.target.value as Toggle }); }}
        >
          {TOGGLES.map(([value, text]) => (
            <option key={value} value={value}>
              {value === "" ? inherit : text}
            </option>
          ))}
        </select>
      </label>
      <label>
        Range check on decode
        <select
          value={settings.decodeCheck}
          onChange={(event) => { set({ decodeCheck: event.target.value as Toggle }); }}
        >
          {TOGGLES.map(([value, text]) => (
            <option key={value} value={value}>
              {value === "" ? inherit : text}
            </option>
          ))}
        </select>
      </label>
    </fieldset>
  );
}
