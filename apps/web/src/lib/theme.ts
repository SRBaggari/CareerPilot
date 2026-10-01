/** The colour theme: chosen in Settings, remembered in this browser only. */
export type Theme = "system" | "light" | "dark";

export const THEMES: { value: Theme; label: string }[] = [
  { value: "system", label: "Match my device" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export const THEME_KEY = "careerpilot-theme";

export function storedTheme(): Theme {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
}

export function saveTheme(theme: Theme) {
  try {
    if (theme === "system") window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Storage unavailable (e.g. a private window): the choice lasts for this page only.
  }
  applyTheme(theme);
}

/** Runs before the page paints, so a saved theme never flashes the other one. */
export const THEME_SCRIPT = `try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

const listeners = new Set<() => void>();

/** Subscribe to theme changes made in this tab (for useSyncExternalStore). */
export function subscribeTheme(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function chooseTheme(theme: Theme) {
  saveTheme(theme);
  listeners.forEach((l) => l());
}
