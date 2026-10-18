import { useCallback, useEffect, useRef, useState } from "react";
import { fmtTime } from "../api";
import type { SongOverview } from "../types";
import { Icon } from "./Icons";

interface Props {
  overview: SongOverview;
  audioUrl: string;
  start: number;
  end: number;
  holdSeconds: number;
  onChange: (start: number, end: number) => void;
}

const H = 132;
const HANDLE_PX = 14;
const LENGTHS = [15, 30, 45, 60];

type Drag = { kind: "start" | "end" | "move" | "new"; anchor: number; offset: number; moved: boolean };

/** Waveform with beats and energy; drag the handles or the selection (mouse, touch or pencil). */
export function Waveform({ overview, audioUrl, start, end, holdSeconds, onChange }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const drag = useRef<Drag | null>(null);
  const [playhead, setPlayhead] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [snap, setSnap] = useState(true);
  const [width, setWidth] = useState(0);
  const dur = overview.duration;
  const len = end - start;

  const snapTo = useCallback(
    (t: number, downbeat: boolean) => {
      if (!snap) return t;
      const grid = downbeat && overview.downbeats.length ? overview.downbeats : overview.beats;
      if (!grid.length) return t;
      let best = grid[0];
      for (const b of grid) if (Math.abs(b - t) < Math.abs(best - t)) best = b;
      return best;
    },
    [snap, overview],
  );

  // keep the canvas sharp at its displayed width
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const ro = new ResizeObserver(() => setWidth(c.clientWidth));
    ro.observe(c);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const c = canvasRef.current;
    if (!c || !width) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.round(width * dpr);
    c.height = Math.round(H * dpr);
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const css = getComputedStyle(c);
    const col = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
    const accent = col("--accent", "#3fc1b0");
    const x = (t: number) => (t / dur) * width;
    g.clearRect(0, 0, width, H);
    // energy
    g.fillStyle = col("--wave-energy", "rgba(255,179,71,0.13)");
    g.beginPath();
    g.moveTo(0, H);
    overview.energy_times.forEach((t, i) => g.lineTo(x(t), H - overview.energy[i] * (H - 20)));
    g.lineTo(width, H);
    g.fill();
    // selection
    g.fillStyle = col("--wave-sel", "rgba(63,193,176,0.16)");
    g.fillRect(x(start), 0, x(end) - x(start), H);
    // final hold zone
    g.fillStyle = col("--wave-hold", "rgba(255,179,71,0.18)");
    g.fillRect(x(Math.max(start, end - holdSeconds)), 0, x(end) - x(Math.max(start, end - holdSeconds)), H);
    // peaks
    const n = overview.peaks.length;
    const mid = (H - 14) / 2;
    for (let i = 0; i < n; i++) {
      const t = (i / n) * dur;
      const h = Math.max(1, overview.peaks[i] * (mid - 4));
      g.fillStyle = t >= start && t <= end ? col("--wave-on", "#d7e3f4") : col("--wave-off", "#5b6475");
      g.fillRect((i / n) * width, mid - h, Math.max(1, width / n - 0.3), 2 * h);
    }
    // beats
    g.fillStyle = col("--wave-beat", "rgba(255,255,255,0.22)");
    for (const b of overview.beats) g.fillRect(x(b), H - 8, 1, 8);
    g.fillStyle = col("--wave-down", "rgba(255,255,255,0.55)");
    for (const b of overview.downbeats) g.fillRect(x(b), H - 14, 1.5, 14);
    // handles
    g.fillStyle = accent;
    for (const t of [start, end]) {
      g.fillRect(x(t) - 1.5, 0, 3, H);
      g.beginPath();
      g.roundRect(x(t) - 6, H / 2 - 16, 12, 32, 4);
      g.fill();
    }
    if (playhead !== null) {
      g.fillStyle = col("--danger", "#ff6b6b");
      g.fillRect(x(playhead) - 1, 0, 2, H);
    }
  }, [overview, start, end, playhead, dur, width, holdSeconds]);

  const timeAt = (clientX: number) => {
    const r = canvasRef.current!.getBoundingClientRect();
    return Math.min(dur, Math.max(0, ((clientX - r.left) / r.width) * dur));
  };
  const pxPerSec = () => (canvasRef.current?.getBoundingClientRect().width ?? 1) / dur;

  const onPointerDown = (e: React.PointerEvent) => {
    const t = timeAt(e.clientX);
    const tol = HANDLE_PX / pxPerSec();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    if (Math.abs(t - start) <= tol) drag.current = { kind: "start", anchor: t, offset: 0, moved: false };
    else if (Math.abs(t - end) <= tol) drag.current = { kind: "end", anchor: t, offset: 0, moved: false };
    else if (t > start && t < end) drag.current = { kind: "move", anchor: t, offset: t - start, moved: false };
    else drag.current = { kind: "new", anchor: snapTo(t, true), offset: 0, moved: false };
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    const t = timeAt(e.clientX);
    const c = canvasRef.current;
    if (c && !d) {
      const tol = HANDLE_PX / pxPerSec();
      c.style.cursor = Math.abs(t - start) <= tol || Math.abs(t - end) <= tol ? "ew-resize" : t > start && t < end ? "grab" : "crosshair";
    }
    if (!d) return;
    d.moved = true;
    const minLen = 3;
    if (d.kind === "start") onChange(Math.min(snapTo(t, true), end - minLen), end);
    else if (d.kind === "end") onChange(start, Math.max(snapTo(t, false), start + minLen));
    else if (d.kind === "move") {
      const s = Math.min(Math.max(0, snapTo(t - d.offset, true)), dur - len);
      onChange(s, s + len);
    } else {
      const s = Math.min(d.anchor, snapTo(t, false));
      const en = Math.max(d.anchor, snapTo(t, false));
      if (en - s >= minLen) onChange(s, en);
    }
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (d && !d.moved && d.kind === "new") {
      // a tap outside the selection moves it there, keeping its length
      const s = Math.min(Math.max(0, d.anchor), Math.max(0, dur - len));
      onChange(s, s + len);
    }
    (e.target as HTMLElement).releasePointerCapture?.(e.pointerId);
  };

  const setLength = (L: number) => {
    const target = start + Math.min(L, dur - start);
    let en = snapTo(target, false);
    if (en <= start + 1) en = Math.min(dur, start + L);
    onChange(start, en);
  };

  // playback limited to the selection
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      const a = audioRef.current;
      if (!a) return;
      setPlayhead(a.currentTime);
      if (a.currentTime >= end) {
        a.pause();
        setPlaying(false);
        setPlayhead(null);
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, end]);

  const toggle = async () => {
    const a = audioRef.current;
    if (!a) return;
    if (playing) {
      a.pause();
      setPlaying(false);
      setPlayhead(null);
      return;
    }
    a.currentTime = start;
    try {
      await a.play();
      setPlaying(true);
    } catch {
      setPlaying(false);
    }
  };

  return (
    <div className="waveform">
      <canvas
        ref={canvasRef}
        style={{ height: H }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => (drag.current = null)}
        aria-label="Song waveform: drag the handles to choose the part of the song"
      />
      <div className="waveform-bar">
        <button type="button" className="btn primary round" onClick={toggle} aria-label={playing ? "Stop" : "Play selection"}>
          <Icon name={playing ? "pause" : "play"} />
        </button>
        <div className="waveform-times">
          <strong>
            {fmtTime(start)} → {fmtTime(end)}
          </strong>
          <span className="muted small">
            {len.toFixed(1)} s · {overview.tempo.toFixed(0)} BPM
          </span>
        </div>
        <div className="seg-buttons" role="group" aria-label="Reel length">
          {LENGTHS.filter((L) => L <= dur + 0.5).map((L) => (
            <button key={L} type="button" className={`chip ${Math.abs(len - L) < 0.8 ? "on" : ""}`} onClick={() => setLength(L)}>
              {L}s
            </button>
          ))}
          <button
            type="button"
            className="chip"
            onClick={() => onChange(overview.suggested_start, overview.suggested_end)}
            title="The most energetic part of the song"
          >
            Suggested
          </button>
        </div>
        <label className="check">
          <input type="checkbox" checked={snap} onChange={(e) => setSnap(e.target.checked)} /> Snap to beats
        </label>
      </div>
      <audio ref={audioRef} src={audioUrl} preload="auto" onEnded={() => setPlaying(false)} />
    </div>
  );
}
