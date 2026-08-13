"use client";

/** Design system §7c — corner circle toggle with the radial View Transition
 *  sweep. The sliver IS the hit area; fill is the target theme's sidebar. */

import { useCallback } from "react";
import { persistTheme, type Theme } from "@/lib/theme";
import { useTheme } from "@/lib/useTheme";

const SIDEBAR: Record<Theme, string> = { light: "#f5f5f5", dark: "#1f1f1f" };

export default function ThemeToggle() {
  const theme = useTheme();
  const target: Theme = theme === "dark" ? "light" : "dark";

  const toggle = useCallback(() => {
    const apply = () => {
      document.documentElement.setAttribute("data-theme", target);
      persistTheme(target);
    };
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!document.startViewTransition || reduced) {
      apply();
      return;
    }
    const transition = document.startViewTransition(apply);
    void transition.ready.then(() => {
      const radius = Math.hypot(window.innerWidth, window.innerHeight);
      document.documentElement.animate(
        { clipPath: ["circle(0px at 0px 0px)", `circle(${radius}px at 0px 0px)`] },
        {
          duration: 700,
          easing: "cubic-bezier(0.4, 0, 0.2, 1)",
          pseudoElement: "::view-transition-new(root)",
        },
      );
    });
  }, [target]);

  if (!theme) return null;

  return (
    <button
      onClick={toggle}
      aria-label={`Switch to ${target} theme`}
      title={`Switch to ${target} theme`}
      className="group fixed left-0 top-0 z-50 size-8 -translate-x-1/2 -translate-y-1/2 rounded-full"
    >
      <span
        className="block size-full rounded-full border border-border transition-transform duration-300 ease-out group-hover:scale-150"
        style={{ background: SIDEBAR[target] }}
      />
    </button>
  );
}
