"use client";

// gillu.me design system §7b — the applied theme as an external store. Widgets
// (charts, the map) must read this, never prefers-color-scheme directly.

import { useSyncExternalStore } from "react";
import type { Theme } from "./theme";

function subscribeTheme(onChange: () => void) {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  mq.addEventListener("change", onChange);
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  return () => {
    mq.removeEventListener("change", onChange);
    observer.disconnect();
  };
}

function getTheme(): Theme {
  const forced = document.documentElement.getAttribute("data-theme");
  if (forced === "light" || forced === "dark") return forced;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** null during SSR/hydration — treat as "not ready" and render a placeholder. */
export function useTheme(): Theme | null {
  return useSyncExternalStore(subscribeTheme, getTheme, () => null);
}
