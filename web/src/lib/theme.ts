// Light / dark theme. The theme lives on <html data-theme="..."> (set before first paint
// by the inline script in index.html, so there's no flash) and is remembered in localStorage.

import { useSyncExternalStore } from "react";

export type Theme = "dark" | "light";

const KEY = "pitwall-theme";
const EVENT = "pitwall-themechange";
const META_COLOUR: Record<Theme, string> = { dark: "#0B0B0F", light: "#F3F3F6" };

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function setTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", META_COLOUR[theme]);
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // private window / storage blocked: the choice just won't be remembered
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(EVENT, onChange);
  return () => window.removeEventListener(EVENT, onChange);
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, currentTheme, () => "dark");
}
