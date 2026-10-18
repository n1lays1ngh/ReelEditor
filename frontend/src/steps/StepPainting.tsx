import { api, fmtDuration, fmtTime } from "../api";
import { AssetGrid } from "../components/AssetGrid";
import { DropZone } from "../components/DropZone";
import { JobProgress } from "../components/JobProgress";
import { EVENT_LABELS, ProgressTimeline } from "../components/ProgressTimeline";
import type { AnalysisCurves, Asset, Job } from "../types";

interface Props {
  video: { path: string; name: string } | null;
  assets: Asset[];
  assetsLoading: boolean;
  maxMb: number;
  analysis: AnalysisCurves | null;
  analysisJob: Job | null;
  analysisError: string | null;
  onSelect: (a: { path: string; name: string }) => void;
  onDeleted: (path: string) => void;
  onRetry: () => void;
  onCancelAnalysis: () => void;
}

const EXT = [".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"];

export function StepPainting(p: Props) {
  const a = p.analysis;
  const running = p.analysisJob && !["done", "failed", "cancelled"].includes(p.analysisJob.state);
  return (
    <div className="step-body">
      <header className="step-header">
        <h2>Your painting video</h2>
        <p className="muted">Upload the overhead recording of your painting session. It can be long — PaintReel finds the moments worth showing.</p>
      </header>

      <DropZone
        kind="video"
        title="Drop your painting video here, or click to browse"
        hint={`MP4, MOV, MKV, AVI, WebM · up to ${p.maxMb >= 1000 ? `${(p.maxMb / 1000).toFixed(0)} GB` : `${p.maxMb} MB`}`}
        accept="video/*,.mkv"
        extensions={EXT}
        maxMb={p.maxMb}
        compact={p.assets.length > 0}
        onUploaded={p.onSelect}
      />
      {p.assets.length > 0 && <h3 className="section-title">Your videos</h3>}
      <AssetGrid
        kind="video"
        assets={p.assets}
        selected={p.video?.path ?? null}
        loading={p.assetsLoading}
        onSelect={(x) => p.onSelect({ path: x.path, name: x.name })}
        onDeleted={p.onDeleted}
      />

      {p.video && (
        <section className="card analysis-card">
          <div className="card-head">
            <h3>{p.video.name}</h3>
            {a && <span className="badge done">Ready</span>}
          </div>
          {running && p.analysisJob && (
            <>
              <JobProgress job={p.analysisJob} onCancel={p.onCancelAnalysis} />
              <p className="muted small">
                This happens once per video. Long recordings take a few minutes; you can continue choosing music meanwhile.
              </p>
            </>
          )}
          {!running && p.analysisError && (
            <div className="note error">
              {p.analysisError}{" "}
              <button type="button" className="btn small" onClick={p.onRetry}>
                Try again
              </button>
            </div>
          )}
          {a && (
            <>
              <div className="before-after">
                <figure>
                  <img src={api.frameUrl(p.video.path, a.start_time, 640)} alt="Start of the painting" loading="lazy" />
                  <figcaption>Start · {fmtTime(a.start_time, 0)}</figcaption>
                </figure>
                <figure>
                  <img src={api.frameUrl(p.video.path, a.final_time, 640)} alt="Finished painting" loading="lazy" />
                  <figcaption>Finished · {fmtTime(a.final_time, 0)}</figcaption>
                </figure>
              </div>
              <ProgressTimeline a={a} />
              <ul className="facts">
                <li>
                  <strong>{fmtDuration(a.duration)}</strong> recording
                </li>
                <li>
                  <strong>{a.events}</strong> painting moments
                  {a.events > 0 &&
                    ` (${Object.entries(a.event_kinds)
                      .map(([k, n]) => `${n} ${EVENT_LABELS[k as keyof typeof EVENT_LABELS].toLowerCase()}`)
                      .join(", ")})`}
                </li>
                <li>
                  <strong>{a.bad_segments.length}</strong> stretch{a.bad_segments.length === 1 ? "" : "es"} skipped (idle, camera bumps,
                  lighting changes)
                </li>
                <li>{a.canvas_detected ? "Paper found automatically" : "Couldn't find the paper edges — adjust the framing in step 4"}</li>
              </ul>
            </>
          )}
        </section>
      )}
    </div>
  );
}
