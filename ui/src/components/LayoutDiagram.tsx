import { useEffect, useState } from "react";
import type { Api } from "../api";

interface FieldLayout {
  path: string;
  type: string;
  offset_min: number;
  offset_max: number;
  size_min: number;
  size_max: number;
  bit_high: number | null;
  bit_low: number | null;
}

interface MessageLayout {
  name: string;
  size_min: number;
  size_max: number;
  fields: FieldLayout[];
}

interface InterfaceLayout {
  name: string;
  max_frame: number;
  link_ram: number;
}

interface ProfileLayout {
  profile: string | null;
  messages: MessageLayout[];
  interfaces: InterfaceLayout[];
  link_ram: number;
}

const range = (low: number, high: number) => (low === high ? String(low) : `${String(low)}..${String(high)}`);

/** Byte layout of a message (offsets, sizes, bit positions) and the RAM of the links. */
export function LayoutDiagram({ api, project, etag }: { api: Api; project: string; etag: string }) {
  const [profiles, setProfiles] = useState<ProfileLayout[]>([]);
  const [profile, setProfile] = useState(0);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    void api.analyze(project).then((result) => {
      if (!active) return;
      const report = (result.data as { profiles?: ProfileLayout[] }).profiles ?? [];
      setProfiles(report);
    });
    return () => {
      active = false;
    };
  }, [api, project, etag]);

  const current = profiles[profile];
  const selected = current?.messages.find((item) => item.name === message) ?? current?.messages[0];
  const width = Math.max(selected?.size_max ?? 1, 1);

  return (
    <section aria-label="Layout">
      <h2>Layout</h2>
      {profiles.length > 1 && (
        <label>
          Layout profile
          <select value={profile} onChange={(event) => { setProfile(Number(event.target.value)); }}>
            {profiles.map((item, index) => (
              <option key={item.profile ?? index} value={index}>
                {item.profile ?? "default"}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Layout message
        <select value={selected?.name ?? ""} onChange={(event) => { setMessage(event.target.value); }}>
          {(current?.messages ?? []).map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      {selected !== undefined && (
        <table aria-label="Message layout">
          <caption>
            {selected.name}: {range(selected.size_min, selected.size_max)} bytes
          </caption>
          <thead>
            <tr>
              <th>field</th>
              <th>type</th>
              <th>offset</th>
              <th>size</th>
              <th>bits</th>
              <th>bytes</th>
            </tr>
          </thead>
          <tbody>
            {selected.fields.map((field) => (
              <tr key={field.path}>
                <td>{field.path}</td>
                <td>{field.type}</td>
                <td>{range(field.offset_min, field.offset_max)}</td>
                <td>{range(field.size_min, field.size_max)}</td>
                <td>{field.bit_high === null || field.bit_low === null ? "" : range(field.bit_high, field.bit_low)}</td>
                <td>
                  <div
                    className="bar"
                    style={{
                      marginLeft: `${String((100 * field.offset_min) / width)}%`,
                      width: `${String(Math.max((100 * field.size_max) / width, 1))}%`,
                    }}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {current !== undefined && (
        <p aria-label="Link RAM">
          Link RAM: {String(current.link_ram)} bytes (
          {current.interfaces.map((item) => `${item.name} ${String(item.link_ram)}`).join(", ")})
        </p>
      )}
    </section>
  );
}
