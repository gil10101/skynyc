// gillu.me design system §7a — one module owns the theme storage contract.
export type Theme = "light" | "dark";

const COOKIE = "theme";
const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;
const SITE_DOMAIN = "gillu.me";

export function persistTheme(theme: Theme) {
  try {
    localStorage.setItem(COOKIE, theme);
  } catch {
    // private mode — the switch still applies to this page view
  }

  const { hostname, protocol } = window.location;
  const onSiteDomain =
    hostname === SITE_DOMAIN || hostname.endsWith(`.${SITE_DOMAIN}`);

  const attributes = [
    `${COOKIE}=${theme}`,
    "path=/",
    `max-age=${ONE_YEAR_SECONDS}`,
    "samesite=lax",
  ];
  // A domain attribute the browser can't match is rejected outright, so only
  // send it on the real site; elsewhere the host-only cookie is correct.
  if (onSiteDomain) attributes.push(`domain=.${SITE_DOMAIN}`);
  if (protocol === "https:") attributes.push("secure");

  document.cookie = attributes.join("; ");
}

// Cookie first (may be newer, set on another subdomain), then localStorage.
export const themeInitScript = `try{var m=document.cookie.match(/(?:^|;\\s*)${COOKIE}=(light|dark)/);var t=m?m[1]:localStorage.getItem("${COOKIE}");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}`;
