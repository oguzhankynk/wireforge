import { useState } from "react";
import type { Api } from "../api";

/** Encode a message value to hex and decode hex to a value with the reference codec. */
export function Playground(props: {
  api: Api;
  project: string;
  messages: readonly string[];
  interfaces: readonly string[];
  profiles: readonly string[];
}) {
  const { api, project, messages, interfaces, profiles } = props;
  const [message, setMessage] = useState(messages[0] ?? "");
  const [iface, setIface] = useState("");
  const [profile, setProfile] = useState(profiles[0] ?? "");
  const [value, setValue] = useState("{}");
  const [hex, setHex] = useState("");
  const [output, setOutput] = useState("");

  const target = () => ({
    message,
    ...(iface === "" ? {} : { interface: iface }),
    ...(profile === "" ? {} : { profile }),
  });
  const encode = async () => {
    try {
      const parsed = JSON.parse(value) as Record<string, unknown>;
      const result = await api.encode(project, { ...target(), value: parsed });
      const data = result.data as { payload?: string; frame?: string };
      if (result.ok) setHex(data.frame ?? data.payload ?? "");
      setOutput(result.ok ? `payload ${data.payload ?? ""}${data.frame === undefined ? "" : `\nframe   ${data.frame}`}` : result.text);
    } catch (failure) {
      setOutput(failure instanceof Error ? failure.message : String(failure));
    }
  };
  const decode = async () => {
    try {
      const body = iface === "" ? { ...target(), hex } : { hex, interface: iface, ...(profile === "" ? {} : { profile }) };
      const result = await api.decode(project, body);
      setOutput(result.text);
    } catch (failure) {
      setOutput(failure instanceof Error ? failure.message : String(failure));
    }
  };

  return (
    <section aria-label="Playground">
      <h2>Playground</h2>
      <label>
        Playground message
        <select value={message} onChange={(event) => { setMessage(event.target.value); }}>
          {messages.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </label>
      <label>
        Playground interface
        <select value={iface} onChange={(event) => { setIface(event.target.value); }}>
          <option value="">(payload only)</option>
          {interfaces.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </label>
      {profiles.length > 0 && (
        <label>
          Playground profile
          <select value={profile} onChange={(event) => { setProfile(event.target.value); }}>
            {profiles.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Value (JSON)
        <textarea value={value} onChange={(event) => { setValue(event.target.value); }} />
      </label>
      <button type="button" onClick={() => void encode()}>
        Encode
      </button>
      <label>
        Hex <input value={hex} onChange={(event) => { setHex(event.target.value); }} />
      </label>
      <button type="button" onClick={() => void decode()}>
        Decode
      </button>
      <pre aria-label="Playground output">{output}</pre>
    </section>
  );
}
