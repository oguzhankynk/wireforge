import { useState } from "react";
import { applyTheme, storedTheme, systemTheme, type Theme } from "../theme";

/** Switches between the light and the dark theme. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => storedTheme() ?? systemTheme());
  const other: Theme = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      className="icon"
      aria-label={`Switch to ${other} theme`}
      title={`Switch to ${other} theme`}
      onClick={() => {
        applyTheme(other);
        setTheme(other);
      }}
    >
      {theme === "dark" ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
        </svg>
      )}
    </button>
  );
}
