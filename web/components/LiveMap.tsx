"use client";

/** The hero: full-bleed themed map, dead-reckoned aircraft, and the signature —
 *  radar sweeps on the airport markers fired by real SSE ticks; sweeps stop
 *  when the data stops (spec §4).
 *
 *  Airport markers are native maplibregl.Markers: the map moves them inside
 *  its own render transform, so they track pan/zoom exactly with zero React
 *  work — the previous approach (React re-render per move event) lagged and
 *  could paint a stale frame. Only their colors are updated, imperatively,
 *  when a snapshot or the theme changes. */

import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { advance, MAX_EXTRAPOLATE_S } from "@/lib/deadReckon";
import { altitudeColor, paletteFor, type DataPalette } from "@/lib/palette";
import type { Theme } from "@/lib/theme";
import { useTheme } from "@/lib/useTheme";
import type { Live } from "@/lib/useLive";
import type { Position } from "@/lib/types";

const AIRPORTS: Record<string, { lat: number; lon: number; label: string }> = {
  KJFK: { lat: 40.6413, lon: -73.7781, label: "JFK" },
  KLGA: { lat: 40.7769, lon: -73.874, label: "LGA" },
  KEWR: { lat: 40.6895, lon: -74.1745, label: "EWR" },
};

function styleFor(theme: Theme): maplibregl.StyleSpecification {
  const flavor = theme === "light" ? "light_nolabels" : "dark_nolabels";
  const bg = theme === "light" ? "#ffffff" : "#1a1a1a";
  return {
    version: 8,
    sources: {
      carto: {
        type: "raster",
        tiles: [
          `https://a.basemaps.cartocdn.com/${flavor}/{z}/{x}/{y}@2x.png`,
          `https://b.basemaps.cartocdn.com/${flavor}/{z}/{x}/{y}@2x.png`,
        ],
        tileSize: 256,
        attribution:
          '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/attributions">CARTO</a>',
      },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": bg } },
      {
        id: "carto",
        type: "raster",
        source: "carto",
        paint: { "raster-opacity": theme === "light" ? 1 : 0.85 },
      },
    ],
  };
}

interface Hover {
  x: number;
  y: number;
  p: Position;
}

interface MarkerRefs {
  marker: maplibregl.Marker;
  ring: HTMLSpanElement;
  label: HTMLSpanElement;
  sweepHost: HTMLSpanElement;
}

export default function LiveMap({ live, paused }: { live: Live; paused: boolean }) {
  const theme = useTheme();
  // Canvas + raster tiles can't re-theme through props — remount per theme
  // (design system §7b rule 3). Placeholder keeps hydration identical.
  if (!theme) {
    return (
      <div
        aria-hidden="true"
        className="w-full border-b border-border"
        style={{ height: "68vh", minHeight: 440 }}
      />
    );
  }
  return <ThemedMap key={theme} theme={theme} live={live} paused={paused} />;
}

function ThemedMap({ theme, live, paused }: { theme: Theme; live: Live; paused: boolean }) {
  const palette = paletteFor(theme);
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const markersRef = useRef<Record<string, MarkerRefs>>({});
  const [hover, setHover] = useState<Hover | null>(null);
  const positionsRef = useRef<{ at: number; list: Position[] }>({ at: 0, list: [] });
  const liveRef = useRef(live);
  liveRef.current = live;
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const paletteRef = useRef<DataPalette>(palette);
  paletteRef.current = palette;

  // map + native markers init
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: styleFor(theme),
      center: [-73.93, 40.71],
      zoom: 9.1,
      minZoom: 7.5,
      maxZoom: 12,
      attributionControl: { compact: true },
      dragRotate: false,
    });
    // zoom control bottom-right: standard map position, clear of the status bar
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    mapRef.current = map;

    for (const [icao, a] of Object.entries(AIRPORTS)) {
      const el = document.createElement("div");
      // no utility classes here: maplibre owns this element's positioning, and
      // our utilities layer would override its position:absolute (the ring
      // would flow to the container's left edge while the label floated)
      el.style.pointerEvents = "none";
      el.innerHTML = `
        <span data-sweep style="position:absolute;left:50%;top:50%;margin-left:-32px;margin-top:-32px;display:block;height:64px;width:64px;border-radius:9999px;border:1px solid transparent;opacity:0"></span>
        <span data-ring style="display:block;height:12px;width:12px;transform:rotate(45deg);border:2px solid transparent;background:var(--background)"></span>
        <span data-label style="position:absolute;left:50%;top:16px;transform:translateX(-50%);font-family:var(--font-geist-mono),monospace;font-size:11px;font-weight:700;letter-spacing:0.05em">${a.label}</span>`;
      const marker = new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat([a.lon, a.lat])
        .addTo(map);
      markersRef.current[icao] = {
        marker,
        ring: el.querySelector("[data-ring]")!,
        label: el.querySelector("[data-label]")!,
        sweepHost: el.querySelector("[data-sweep]")!,
      };
    }

    // layout can settle after hydration; a zero-height first measure sticks otherwise
    const settle = setTimeout(() => map.resize(), 250);
    map.once("load", () => map.resize());
    return () => {
      clearTimeout(settle);
      for (const m of Object.values(markersRef.current)) m.marker.remove();
      markersRef.current = {};
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // theme changes remount the whole component (key)

  // absorb each snapshot; recolor markers + fire the sweep imperatively —
  // zero React work on the map layer per tick
  useEffect(() => {
    if (live.snap) positionsRef.current = { at: Date.now(), list: live.snap.positions };
    const conditions = live.snap?.conditions ?? [];
    for (const [icao, refs] of Object.entries(markersRef.current)) {
      const cat = conditions.find((c) => c.station === icao)?.flight_category ?? null;
      const color = cat
        ? (paletteRef.current.category[cat] ?? paletteRef.current.neutral)
        : paletteRef.current.neutral;
      refs.ring.style.borderColor = color;
      refs.label.style.color = color;
      refs.sweepHost.style.borderColor = color;
      // idle at opacity 0 — the animation carries all visibility, so the
      // sweep truly stops (not lingers) when the data stops
      if (live.mode === "live") {
        refs.sweepHost.classList.remove("radar-ring");
        void refs.sweepHost.offsetWidth; // restart the CSS animation
        refs.sweepHost.classList.add("radar-ring");
      } else {
        refs.sweepHost.classList.remove("radar-ring");
      }
    }
  }, [live.snap, live.mode, live.tick]);

  // render loop — planes on a canvas overlay, dead-reckoned between ticks
  useEffect(() => {
    let frame = 0;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const draw = () => {
      frame = requestAnimationFrame(draw);
      const map = mapRef.current;
      const canvas = canvasRef.current;
      if (!map || !canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const { clientWidth, clientHeight } = map.getContainer();
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== clientWidth * dpr || canvas.height !== clientHeight * dpr) {
        canvas.width = clientWidth * dpr;
        canvas.height = clientHeight * dpr;
        canvas.style.width = `${clientWidth}px`;
        canvas.style.height = `${clientHeight}px`;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, clientWidth, clientHeight);

      const { at, list } = positionsRef.current;
      const mode = liveRef.current.mode;
      const pal = paletteRef.current;
      const dtS =
        pausedRef.current || reduced || mode !== "live" ? 0 : (Date.now() - at) / 1000;

      for (const p of list) {
        const pos =
          p.on_ground || dtS <= 0
            ? { lat: p.lat, lon: p.lon }
            : advance({ lat: p.lat, lon: p.lon }, p.vel_ms, p.track_deg, dtS);
        const point = map.project([pos.lon, pos.lat]);
        if (point.x < -20 || point.y < -20 || point.x > clientWidth + 20 || point.y > clientHeight + 20) continue;
        const stale = p.age_s + dtS > MAX_EXTRAPOLATE_S + 60;
        ctx.save();
        ctx.translate(point.x, point.y);
        ctx.rotate(((p.track_deg ?? 0) * Math.PI) / 180);
        ctx.globalAlpha = stale ? 0.35 : 0.95;
        // halo first so glyphs stay legible over any tile luminance
        ctx.beginPath();
        ctx.moveTo(0, -7);
        ctx.lineTo(4.6, 6);
        ctx.lineTo(0, 3.2);
        ctx.lineTo(-4.6, 6);
        ctx.closePath();
        ctx.strokeStyle = pal.glyphHalo;
        ctx.lineWidth = 2.5;
        ctx.stroke();
        ctx.fillStyle = altitudeColor(pal, p.alt_m);
        ctx.fill();
        ctx.restore();
      }
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, []);

  // hover picking — on map events, not the overlay canvas: the overlay is
  // pointer-events:none so wheel zoom / drag pan reach the map underneath
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const onMove = (event: maplibregl.MapMouseEvent) => {
      const { x, y } = event.point;
      let best: Hover | null = null;
      let bestD = 14;
      for (const p of positionsRef.current.list) {
        const point = map.project([p.lon, p.lat]);
        const d = Math.hypot(point.x - x, point.y - y);
        if (d < bestD) {
          bestD = d;
          best = { x: point.x, y: point.y, p };
        }
      }
      setHover(best);
    };
    const onLeave = () => setHover(null);
    map.on("mousemove", onMove);
    map.on("mouseout", onLeave);
    map.on("movestart", onLeave); // stale tooltip mid-pan/zoom reads as a bug
    return () => {
      map.off("mousemove", onMove);
      map.off("mouseout", onLeave);
      map.off("movestart", onLeave);
    };
  }, []);

  const rampCss = `linear-gradient(90deg, ${palette.altStops.map(([, c]) => c).join(",")})`;

  return (
    <div
      className="relative w-full overflow-hidden border-b border-border"
      style={{ height: "68vh", minHeight: 440 }}
    >
      <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />
      <canvas ref={canvasRef} style={{ position: "absolute", inset: 0, pointerEvents: "none" }} />

      {hover && (
        <div
          className="pointer-events-none absolute z-10 rounded-lg border border-border-2 bg-surface/95 px-3 py-2"
          style={{ left: hover.x + 14, top: hover.y - 10 }}
        >
          <div className="font-mono text-[13px] font-bold text-ink">
            {hover.p.callsign ?? hover.p.icao24}
          </div>
          <div className="mt-1 grid grid-cols-2 gap-x-4 font-mono text-[11px] text-ink-2">
            <span>{hover.p.alt_m != null ? `${Math.round(hover.p.alt_m)} m` : "alt —"}</span>
            <span>{hover.p.vel_ms != null ? `${Math.round(hover.p.vel_ms * 1.944)} kt` : "spd —"}</span>
            <span>
              {hover.p.vrate_ms != null
                ? `${hover.p.vrate_ms > 0 ? "↑" : hover.p.vrate_ms < 0 ? "↓" : "→"} ${Math.abs(hover.p.vrate_ms).toFixed(1)} m/s`
                : "vs —"}
            </span>
            <span>{hover.p.age_s}s old</span>
          </div>
        </div>
      )}

      {/* altitude legend */}
      <div className="absolute bottom-8 left-3 rounded-lg border border-border bg-page/80 px-3 py-2">
        <div className="font-mono text-[10px] tracking-widest text-muted">ALTITUDE</div>
        <div className="mt-1 h-1.5 w-36 rounded-full" style={{ background: rampCss }} />
        <div className="mt-0.5 flex justify-between font-mono text-[9.5px] text-faint">
          <span>0</span>
          <span>3 km</span>
          <span>12 km</span>
        </div>
      </div>
    </div>
  );
}
