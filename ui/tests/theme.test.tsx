// The theme toggle: the system theme first, then the user's choice, which is remembered.
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { ThemeToggle } from "../src/components/ThemeToggle";
import { storedTheme } from "../src/theme";

afterEach(() => {
  cleanup();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe("theme", () => {
  it("switches between light and dark and remembers the choice", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    expect(storedTheme()).toBeNull();
    await user.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(storedTheme()).toBe("dark");
    await user.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(storedTheme()).toBe("light");
  });

  it("starts from a stored choice", () => {
    localStorage.setItem("wireforge.theme", "dark");
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: "Switch to light theme" })).toBeTruthy();
  });
});
