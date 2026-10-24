import { useState } from "react";
import type { Api, ServiceResult } from "../api";
import { numberOrExpression } from "../model";

/** Creates or updates a checksum declaration (built-in CRC-32 or a parametric CRC). */
export function ChecksumEditor({
  api,
  project,
  onSaved,
}: {
  api: Api;
  project: string;
  onSaved: (result: ServiceResult) => void;
}) {
  const [name, setName] = useState("");
  const [algo, setAlgo] = useState("crc32");
  const [width, setWidth] = useState("16");
  const [poly, setPoly] = useState("0x1021");
  const [init, setInit] = useState("0xFFFF");
  const [error, setError] = useState("");

  const save = async () => {
    const definition: Record<string, unknown> = { algo };
    if (algo === "crc") {
      Object.assign(definition, {
        width: Number(width),
        poly: numberOrExpression(poly),
        init: numberOrExpression(init),
      });
    }
    try {
      onSaved(await api.putEntry(project, "checksums", name.trim(), { definition }));
      setError("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  };

  return (
    <section aria-label="Checksum editor">
      <h2>Checksum</h2>
      <label>
        Checksum name <input value={name} onChange={(event) => { setName(event.target.value); }} />
      </label>
      <label>
        Algorithm
        <select value={algo} onChange={(event) => { setAlgo(event.target.value); }}>
          <option value="crc32">crc32</option>
          <option value="crc">crc</option>
        </select>
      </label>
      {algo === "crc" && (
        <>
          <label>
            Width <input value={width} onChange={(event) => { setWidth(event.target.value); }} />
          </label>
          <label>
            Polynomial <input value={poly} onChange={(event) => { setPoly(event.target.value); }} />
          </label>
          <label>
            Initial value <input value={init} onChange={(event) => { setInit(event.target.value); }} />
          </label>
        </>
      )}
      <button type="button" className="primary" disabled={name.trim() === ""} onClick={() => void save()}>
        Save checksum
      </button>
      {error !== "" && <p role="alert">{error}</p>}
    </section>
  );
}
