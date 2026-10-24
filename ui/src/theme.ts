// Light or dark theme: the system setting until the user picks one, which is remembered in this
// browser.
export type Theme = "light" | "dark";

const KEY = "wireforge.theme";

/** The theme the user picked earlier, if any. */
export function storedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

/** The theme of the system setting. */
export function systemTheme(): Theme {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

/** Shows `theme` (without `remember`, a stored choice is only applied, not written). */
export function applyTheme(theme: Theme, remember = true): void {
  document.documentElement.dataset.theme = theme;
  if (!remember) return;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // storage blocked: the choice lasts until the page is reloaded
  }
}
