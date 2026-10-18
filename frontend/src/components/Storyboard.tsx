import { useState } from "react";
import { api, fmtTime } from "../api";
import type { PlanPreview, StoryboardItem } from "../types";

export const STYLE_LABELS: Record<string, { label: string; help: string }> = {
  opening: { label: "Opening", help: "The blank paper and first strokes" },
  jump: { label: "Jump", help: "A painting step pops in on the beat" },
  flow: { label: "Flow", help: "A smooth timelapse to the next moment" },
  moment: { label: "Moment", help: "Real brushwork, close to real time" },
  timelapse: { label: "Timelapse", help: "Continuous timelapse" },
  hold: { label: "Finish", help: "The finished painting" },
};

interface Props {
  video: string;
  plan: PlanPreview | null;
  loading: boolean;
  error: string | null;
}

/** Thumbnail strip of the planned edit: one tile per segment, width = its share of the reel. */
export function Storyboard({ video, plan, loading, error }: Props) {
  const [active, setActive] = useState<StoryboardItem | null>(null);
  if (error) return <div className="storyboard-empty error-text">{error}</div>;
  if (!plan) {
    return <div className={`storyboard-empty ${loading ? "loading" : ""}`}>{loading ? "Planning your reel…" : "No plan yet."}</div>;
  }
  const total = plan.edl.segments[plan.edl.segments.length - 1]?.out_end ?? 1;
  const s = plan.summary as Record<string, number | string | string[]>;
  const cuts = plan.storyboard.length - 1;
  const shown = active ?? null;
  return (
    <div className={`storyboard ${loading ? "refreshing" : ""}`}>
      <div className="story-strip" role="list">
        {plan.storyboard.map((it) => (
          <button
            key={it.index}
            type="button"
            role="listitem"
            className={`story-tile st-${it.style ?? "flow"} ${shown?.index === it.index ? "active" : ""} ${it.transition === "xfade" ? "xfade" : ""}`}
            style={{ flexGrow: Math.max(0.0001, it.out_end - it.out_start), flexBasis: 0 }}
            onMouseEnter={() => setActive(it)}
            onFocus={() => setActive(it)}
            onClick={() => setActive(it)}
            title={`${STYLE_LABELS[it.style ?? "flow"]?.label ?? it.style} · ${fmtTime(it.out_start)}–${fmtTime(it.out_end)}`}
          >
            <img src={api.thumbUrl(video, it.src_end, true, 200)} alt="" loading="lazy" draggable={false} />
          </button>
        ))}
      </div>
      <div className="story-ruler" aria-hidden="true">
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <span key={f} style={{ left: `${f * 100}%` }}>
            {(f * total).toFixed(f === 0 ? 0 : 1)}s
          </span>
        ))}
      </div>
      <div className="story-info">
        {shown ? (
          <>
            <span className={`style-pill st-${shown.style ?? "flow"}`}>{STYLE_LABELS[shown.style ?? "flow"]?.label}</span>
            <span>
              {fmtTime(shown.out_start)} → {fmtTime(shown.out_end)} in the reel
            </span>
            <span className="muted">
              {shown.event ? `· ${shown.event.replace("_", " ")}` : ""} · painting {fmtTime(shown.src_start, 0)}–{fmtTime(shown.src_end, 0)}
              {shown.transition === "xfade" ? " · crossfade in" : ""}
            </span>
          </>
        ) : (
          <span className="muted">
            {cuts} cuts · {String(s.events_shown ?? 0)} of {plan.events_total} painting moments · {String(s.xfades ?? 0)} crossfades. Hover a
            tile for details.
          </span>
        )}
      </div>
      <div className="story-legend">
        {Object.entries(STYLE_LABELS)
          .filter(([k]) => plan.storyboard.some((it) => it.style === k))
          .map(([k, v]) => (
            <span key={k} className="legend-item" title={v.help}>
              <i className={`dot st-${k}`} /> {v.label}
            </span>
          ))}
      </div>
    </div>
  );
}
