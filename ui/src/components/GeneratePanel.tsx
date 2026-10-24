import { useEffect, useState } from "react";
import { ApiError, type Api, type Language } from "../api";

/** Every generated file of the project, and the download of the code with its test project. */
export function GeneratePanel({ api, project, etag }: { api: Api; project: string; etag: string }) {
  const [files, setFiles] = useState<Record<string, string>>({});
  const [file, setFile] = useState("");
  const [language, setLanguage] = useState<Language>("cpp");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void api.preview(project, language).then((result) => {
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
  }, [api, project, etag, language]);

  const download = async () => {
    setError("");
    try {
      const archive = await api.archive(project, language);
      const url = URL.createObjectURL(archive);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${project}${language === "c" ? "-c" : ""}-generated.zip`;
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
