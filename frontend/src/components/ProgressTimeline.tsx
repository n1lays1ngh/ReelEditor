import { useState } from "react";
import { fmtTime } from "../api";
import type { AnalysisCurves, EventKind, VisualEvent } from "../types";

export const EVENT_LABELS: Record<EventKind, string> = {
  wash: "Wash",
  new_color: "New colour",
  detail: "Detail",
  bloom: "Bloom",
  change: "Change",
};

const W = 1000;
const H = 150;
const TOP = 26; // event lane
const PLOT_H = H - TOP - 18;

/** Painting progress over the source video, with detected moments and excluded stretches. */
export function ProgressTimeline({ a }: { a: AnalysisCurves }) {
  const [hover, setHover] = useState<VisualEvent | null>(null);
  const dur = Math.max(a.duration, 1);
  const x = (t: number) => (t / dur) * W;
  const y = (v: number) => TOP + (1 - v) * PLOT_H;
  const line = (vals: number[]) => a.times.map((t, i) => `${i ? "L" : "M"}${x(t).toFixed(1)},${y(vals[i]).toFixed(1)}`).join("");
  const area = `${line(a.progress)}L${x(a.times[a.times.length - 1] ?? 0)},${y(0)}L${x(a.times[0] ?? 0)},${y(0)}Z`;
  const ticks = niceTicks(dur);

  return (
    <div className="timeline">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Painting progress over time">
        <path className="tl-change" d={line(a.visual_change)} />
        <path className="tl-area" d={area} />
        {a.bad_segments.map((b, i) => (
          <rect key={i} className="tl-bad" x={x(b.start)} y={TOP} width={Math.max(2, x(b.end) - x(b.start))} height={PLOT_H}>
            <title>
              Skipped {fmtTime(b.start, 0)}–{fmtTime(b.end, 0)}: {b.reason}
            </title>
          </rect>
        ))}
        <path className="tl-progress" d={line(a.progress)} />
        <line className="tl-mark start" x1={x(a.start_time)} x2={x(a.start_time)} y1={TOP} y2={TOP + PLOT_H} />
        <line className="tl-mark final" x1={x(a.final_time)} x2={x(a.final_time)} y1={TOP} y2={TOP + PLOT_H} />
        {a.events_list.map((e, i) => (
          <g key={i} onMouseEnter={() => setHover(e)} onMouseLeave={() => setHover(null)} onClick={() => setHover(e)}>
            <line className={`tl-ev-line ev-${e.kind}`} x1={x(e.time)} x2={x(e.time)} y1={TOP - 4} y2={y(e.progress)} />
            <circle className={`tl-ev ev-${e.kind}`} cx={x(e.time)} cy={12} r={4 + 5 * e.strength} />
          </g>
        ))}
        {ticks.map((t) => (
          <text key={t} className="tl-tick" x={x(t)} y={H - 3}>
            {fmtTime(t, 0)}
          </text>
        ))}
      </svg>
      <div className="timeline-legend">
        {(Object.keys(EVENT_LABELS) as EventKind[])
          .filter((k) => a.event_kinds[k])
          .map((k) => (
            <span key={k} className="legend-item">
              <i className={`dot ev-${k}`} /> {EVENT_LABELS[k]} · {a.event_kinds[k]}
            </span>
          ))}
        {a.bad_segments.length > 0 && (
          <span className="legend-item">
            <i className="dot bad" /> Skipped ({a.bad_segments.length})
          </span>
        )}
        <span className="legend-item muted">
          {hover
            ? `${EVENT_LABELS[hover.kind]} at ${fmtTime(hover.time, 0)} · strength ${Math.round(hover.strength * 100)}%`
            : "Hover a dot to see the moment"}
        </span>
      </div>
    </div>
  );
}

function niceTicks(dur: number): number[] {
  const steps = [10, 30, 60, 120, 300, 600, 900, 1800, 3600];
  const step = steps.find((s) => dur / s <= 6) ?? 3600;
  const out: number[] = [];
  for (let t = step; t < dur - step * 0.3; t += step) out.push(t);
  return out;
}
