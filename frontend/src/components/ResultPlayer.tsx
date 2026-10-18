import { useState } from "react";
import { api, errorMessage, fmtTime, stem } from "../api";
import { useNotify } from "../state";
import type { Job } from "../types";
import { Icon } from "./Icons";
import { STYLE_LABELS } from "./Storyboard";

interface Props {
  job: Job;
  onTryAnother: () => void;
  tryLabel?: string;
}

export function downloadName(job: Job, silent: boolean): string {
  const p = job.params as Record<string, unknown>;
  const edl = p.edl as { video?: { path: string }; song?: { path: string } } | undefined;
  const video = stem(String(p.video ?? edl?.video?.path ?? "painting"));
  const song = stem(String(p.song ?? edl?.song?.path ?? "song"));
  const preset = String(p.preset ?? "reel");
  return `${video}-${song}-${preset}${silent ? "-no-music" : ""}.mp4`;
}

/** The finished reel in a phone frame, with downloads, sharing and "how this was edited". */
export function ResultPlayer({ job, onTryAnother, tryLabel = "Try another style" }: Props) {
  const [debug, setDebug] = useState(false);
  const [sharing, setSharing] = useState(false);
  const notify = useNotify();
  const hasDebug = Boolean(job.outputs.debug);
  const src = api.fileUrl(job, debug && hasDebug ? job.outputs.debug : job.outputs.reel);
  const s = job.summary as Record<string, unknown>;
  const styles = (s.styles ?? {}) as Record<string, number>;
  const warnings = (s.warnings as string[] | undefined) ?? [];
  const canShare = typeof navigator !== "undefined" && "share" in navigator && "canShare" in navigator;

  const share = async () => {
    setSharing(true);
    try {
      const blob = await (await fetch(api.fileUrl(job, job.outputs.reel))).blob();
      const file = new File([blob], downloadName(job, false), { type: "video/mp4" });
      if (navigator.canShare?.({ files: [file] })) {
        await navigator.share({ files: [file], title: "My painting reel" });
      } else {
        notify("Sharing files isn't supported in this browser — use Download instead.", "info");
      }
    } catch (e) {
      if ((e as DOMException).name !== "AbortError") notify(errorMessage(e), "error");
    } finally {
      setSharing(false);
    }
  };

  return (
    <div className="result">
      <div className="phone">
        <div className="phone-notch" aria-hidden="true" />
        <video
          key={src}
          className="phone-video"
          src={src}
          poster={job.outputs.cover ? api.fileUrl(job, job.outputs.cover) : undefined}
          controls
          playsInline
          preload="metadata"
        />
      </div>
      <div className="result-side">
        <h3>Your reel is ready</h3>
        <p className="muted">
          {Number(s.duration ?? 0).toFixed(1)} s · {String(s.segments ?? "–")} segments · {String(s.events_shown ?? 0)} painting moments ·{" "}
          {String(s.xfades ?? 0)} crossfades
        </p>
        <div className="result-actions">
          <a className="btn primary" href={api.fileUrl(job, job.outputs.reel)} download={downloadName(job, false)}>
            <Icon name="download" /> Download with music
          </a>
          <a className="btn" href={api.fileUrl(job, job.outputs.silent)} download={downloadName(job, true)}>
            <Icon name="download" /> Download without music
          </a>
          {canShare && (
            <button type="button" className="btn" onClick={share} disabled={sharing}>
              <Icon name="share" /> {sharing ? "Preparing…" : "Share / Save to Photos"}
            </button>
          )}
        </div>
        <p className="muted small">The version without music is for adding the song from Instagram's own music library.</p>
        <button type="button" className="btn ghost" onClick={onTryAnother}>
          <Icon name="palette" /> {tryLabel}
        </button>
        {hasDebug && (
          <label className="check">
            <input type="checkbox" checked={debug} onChange={(e) => setDebug(e.target.checked)} /> Show the debug overlay
            version
          </label>
        )}
        {warnings.length > 0 && <div className="note warn">{warnings.join(" ")}</div>}
        <details className="how">
          <summary>How this was edited</summary>
          <ul className="how-list">
            {Object.entries(styles).map(([k, n]) => (
              <li key={k}>
                <i className={`dot st-${k}`} /> {n} × {STYLE_LABELS[k]?.label ?? k} — {STYLE_LABELS[k]?.help ?? ""}
              </li>
            ))}
            <li>
              Song {fmtTime(Number(s.song_start ?? 0))} → {fmtTime(Number(s.song_end ?? 0))}, painting shown at {String(s.min_speed)}× to{" "}
              {String(s.max_speed)}× speed
            </li>
          </ul>
          {job.outputs.mapping_plot && (
            <img className="how-plot" src={api.fileUrl(job, job.outputs.mapping_plot)} alt="Output time to painting time chart" loading="lazy" />
          )}
        </details>
      </div>
    </div>
  );
}
