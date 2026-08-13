"use client";

/** The hero: full-bleed dark map, dead-reckoned aircraft, and the signature —
 *  radar sweeps on the airport markers fired by real SSE ticks. When the data
 *  stops, the sweeps stop: the page visibly flatlines (spec §4). */

import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { advance, MAX_EXTRAPOLATE_S } from "@/lib/deadReckon";
import { altitudeColor, CATEGORY } from "@/lib/palette";
import type { Live } from "@/lib/useLive";
import type { Position } from "@/lib/types";

const AIRPORTS: Record<string, { lat: number; lon: number; label: string }> = {
  KJFK: { lat: 40.6413, lon: -73.7781, label: "JFK" },
  KLGA: { lat: 40.7769, lon: -73.874, label: "LGA" },
  KEWR: { lat: 40.6895, lon: -74.1745, label: "EWR" },
};

const STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    carto: {
      type: "raster",
      tiles: [
        "https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}@2x.png",
        "https://b.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}@2x.png",
      ],
      tileSize: 256,
      attribution:
        '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/attributions">CARTO</a>',
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#0d1117" } },
    { id: "carto", type: "raster", source: "carto", paint: { "raster-opacity": 0.85 } },
  ],
};

interface Hover {
  x: number;
  y: number;
  p: Position;
}

export default function LiveMap({ live, paused }: { live: Live; paused: boolean }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const positionsRef = useRef<{ at: number; list: Position[] }>({ at: 0, list: [] });
  const liveRef = useRef(live);
  liveRef.current = live;
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  // map init
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let map: maplibregl.Map;
    try {
      map = new maplibregl.Map({
      container: containerRef.current,
      style: STYLE,
      center: [-73.93, 40.71],
      zoom: 9.1,
      minZoom: 7.5,
      maxZoom: 12,
      attributionControl: { compact: true },
      dragRotate: false,
      });
    } catch (e) {
      console.error("MAP INIT FAILED", e);
      return;
    }
    map.on("error", (e) => console.error("MAP ERROR", e.error?.message ?? e));
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    mapRef.current = map;
    // layout can settle after hydration; a zero-height first measure sticks otherwise
    const settle = setTimeout(() => map.resize(), 250);
    map.once("load", () => map.resize());
    return () => {
      clearTimeout(settle);
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // absorb each snapshot
  useEffect(() => {
    if (live.snap) positionsRef.current = { at: Date.now(), list: live.snap.positions };
  }, [live.snap]);

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
        ctx.rotate((((p.track_deg ?? 0) - 0) * Math.PI) / 180);
        ctx.globalAlpha = stale ? 0.35 : 0.95;
        ctx.fillStyle = altitudeColor(p.alt_m);
        // plane glyph: slender delta pointing up (north) pre-rotation
        ctx.beginPath();
        ctx.moveTo(0, -7);
        ctx.lineTo(4.6, 6);
        ctx.lineTo(0, 3.2);
        ctx.lineTo(-4.6, 6);
        ctx.closePath();
        ctx.fill();
        ctx.restore();
      }
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, []);

  // hover picking
  useEffect(() => {
    const canvas = canvasRef.current;
    const map = mapRef.current;
    if (!canvas || !map) return;
    const onMove = (event: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
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
    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mouseleave", onLeave);
    return () => {
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mouseleave", onLeave);
    };
  }, []);

  // airport markers + category ring + radar sweep per tick
  const category = (icao: string) =>
    live.snap?.conditions.find((c) => c.station === icao)?.flight_category ?? null;

  return (
    <div className="relative w-full overflow-hidden border-b border-border" style={{ height: "68vh", minHeight: 440 }}>
      <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />
      <canvas ref={canvasRef} style={{ position: "absolute", inset: 0 }} />

      {/* airport overlays live in map space via project(); re-render on tick */}
      <AirportMarkers mapRef={mapRef} tick={live.tick} mode={live.mode} category={category} />

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
      <div className="absolute bottom-8 left-3 rounded-md border border-border bg-page/80 px-3 py-2">
        <div className="font-mono text-[10px] tracking-widest text-muted">ALTITUDE</div>
        <div
          className="mt-1 h-1.5 w-36 rounded-full"
          style={{
            background:
              "linear-gradient(90deg,#440154,#3b528b,#21918c,#5ec962,#fde725)",
          }}
        />
        <div className="mt-0.5 flex justify-between font-mono text-[9.5px] text-faint">
          <span>0</span>
          <span>3 km</span>
          <span>12 km</span>
        </div>
      </div>
    </div>
  );
}

function AirportMarkers({
  mapRef,
  tick,
  mode,
  category,
}: {
  mapRef: React.RefObject<maplibregl.Map | null>;
  tick: number;
  mode: string;
  category: (icao: string) => string | null;
}) {
  const [, force] = useState(0);
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const rerender = () => force((n) => n + 1);
    map.on("move", rerender);
    map.on("resize", rerender);
    return () => {
      map.off("move", rerender);
      map.off("resize", rerender);
    };
  }, [mapRef]);

  const map = mapRef.current;
  if (!map) return null;
  return (
    <>
      {Object.entries(AIRPORTS).map(([icao, a]) => {
        const point = map.project([a.lon, a.lat]);
        const cat = category(icao);
        const ring = cat ? (CATEGORY[cat] ?? "#7d8894") : "#7d8894";
        return (
          <div
            key={icao}
            className="pointer-events-none absolute"
            style={{ left: point.x, top: point.y, transform: "translate(-50%,-50%)" }}
          >
            {/* signature: one radar sweep per real data tick — stops when data stops */}
            {mode === "live" && (
              <span
                key={tick}
                className="radar-ring absolute left-1/2 top-1/2 -ml-8 -mt-8 block h-16 w-16 rounded-full border"
                style={{ borderColor: ring }}
              />
            )}
            <span
              className="block h-3 w-3 rotate-45 border-2 bg-page"
              style={{ borderColor: ring }}
            />
            <span
              className="absolute left-1/2 top-4 -translate-x-1/2 font-mono text-[11px] font-bold tracking-wider"
              style={{ color: ring }}
            >
              {a.label}
            </span>
          </div>
        );
      })}
    </>
  );
}
