import { useEffect, useState } from "react";
import { ApiError, type Api } from "../api";

/** Every generated file of the project, and the download of the code with its test project. */
export function GeneratePanel({ api, project, etag }: { api: Api; project: string; etag: string }) {
  const [files, setFiles] = useState<Record<string, string>>({});
  const [file, setFile] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void api.preview(project).then((result) => {
      if (!active) return;
      const generated = (result.data as { files?: Record<string, string> }).files ?? {};
      setFiles(generated);
      setFile((current) => (current in generated ? current : (Object.keys(generated)[0] ?? "")));
    });
    return () => {
      active = false;
    };
  }, [api, project, etag]);

  const download = async () => {
    setError("");
    try {
      const archive = await api.archive(project);
      const url = URL.createObjectURL(archive);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${project}-generated.zip`;
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
      {names.length === 0 ? (
        <p>No generated code: fix the issues above first.</p>
      ) : (
        <>
          <p>
            The zip holds the generated headers, a GoogleTest project that checks them against the reference codec,
            the runtime and the model. Build and run the tests with{" "}
            <code>cmake -S include/&lt;name&gt;/generated_tests -B build</code>, <code>cmake --build build</code> and{" "}
            <code>ctest --test-dir build</code> (README.md in the zip).
          </p>
          <button type="button" onClick={() => void download()}>
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
