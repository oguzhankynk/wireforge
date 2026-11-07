import { useEffect, useState } from "react";
import { ApiError, type Api, type Language } from "../api";

/** Every generated file of the project, and the download of the code with its test project. */
export function GeneratePanel({
  api,
  project,
  etag,
  nodes = [],
}: {
  api: Api;
  project: string;
  etag: string;
  /** Units of the project; with units the preview and the zip are those of the chosen unit. */
  nodes?: readonly string[];
}) {
  const [files, setFiles] = useState<Record<string, string>>({});
  const [file, setFile] = useState("");
  const [language, setLanguage] = useState<Language>("cpp");
  const [error, setError] = useState("");
  const [chosen, setChosen] = useState("");
  const unit = nodes.length === 0 ? undefined : nodes.includes(chosen) ? chosen : nodes[0];

  useEffect(() => {
    let active = true;
    // A project with units has no preview before a unit is chosen (the units load after the project).
    void api.preview(project, language, unit).catch(() => ({ data: {} })).then((result) => {
      if (!active) return;
      const generated = (result.data as { files?: Record<string, string> }).files ?? {};
      setFiles(generated);
      // The model file first: it holds the types and their functions.
      const names = Object.keys(generated);
      const model = names.find((name) => /\/model\.(hpp|h)$/.test(name));
      setFile((current) => (current in generated ? current : (model ?? names[0] ?? "")));
    });
    return () => {
      active = false;
    };
  }, [api, project, etag, language, unit]);

  const download = async () => {
    setError("");
    try {
      const archive = await api.archive(project, language, unit);
      const url = URL.createObjectURL(archive);
      const link = document.createElement("a");
      link.href = url;
      link.download =
        unit === undefined ? `${project}${language === "c" ? "-c" : ""}-generated.zip` : `${project}-${unit}-generated.zip`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : String(failure));
    }
  };

  const names = Object.keys(files);
  return (
    <section aria-label="Generate">
      <h2>Generate</h2>
      {unit !== undefined && (
        <label>
          Unit
          <select
            value={unit}
            onChange={(event) => {
              setChosen(event.target.value);
            }}
          >
            {nodes.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Language
        <select
          value={language}
          onChange={(event) => { setLanguage(event.target.value === "c" ? "c" : "cpp"); }}
        >
          <option value="cpp">C++17</option>
          <option value="c">C99</option>
        </select>
      </label>
      {names.length === 0 ? (
        <p>No generated code: fix the issues above first.</p>
      ) : (
        <>
          <p>
            The zip holds the generated code, a GoogleTest project that checks it against the reference codec,
            the runtime and the model. Build and run the tests with{" "}
            <code>cmake -S include/&lt;name&gt;/generated_tests -B build</code>, <code>cmake --build build</code> and{" "}
            <code>ctest --test-dir build</code> (README.md in the zip).
          </p>
          <button type="button" className="primary" onClick={() => void download()}>
            Download code and tests
          </button>
          <label>
            File
            <select value={file} onChange={(event) => { setFile(event.target.value); }}>
              {names.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <pre aria-label="Generated file">{files[file] ?? ""}</pre>
        </>
      )}
      {error !== "" && <p role="alert">{error}</p>}
    </section>
  );
}
