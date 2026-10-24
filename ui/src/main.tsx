import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Api } from "./api";
import { App } from "./App";
import "./styles.css";
import { applyTheme, storedTheme } from "./theme";

const stored = storedTheme();
if (stored !== null) applyTheme(stored, false);

const container = document.getElementById("root");
if (container !== null) {
  const token = new URLSearchParams(window.location.search).get("token") ?? undefined;
  createRoot(container).render(
    <StrictMode>
      <App api={new Api("", token)} />
    </StrictMode>,
  );
}
