import type { Job } from "../types";

const STAGE_LABELS: Record<string, string> = {
  queued: "Waiting to start",
  analyzing: "Reading the painting",
  planning: "Planning the edit",
  rendering: "Rendering",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

interface Props {
  job: Job;
  onCancel?: () => void;
}

/** Progress ring + stage text, with a cancel button while running. */
export function JobProgress({ job, onCancel }: Props) {
  const pct = Math.round(job.progress * 100);
  const running = !["done", "failed", "cancelled"].includes(job.state);
  const r = 26;
  const c = 2 * Math.PI * r;
  return (
    <div className={`job-progress ${job.state}`} role="status" aria-live="polite">
      <svg width="64" height="64" viewBox="0 0 64 64" aria-hidden="true">
        <circle className="ring-bg" cx="32" cy="32" r={r} />
        <circle className="ring-fg" cx="32" cy="32" r={r} strokeDasharray={c} strokeDashoffset={c * (1 - job.progress)} />
        <text x="32" y="37" textAnchor="middle">
          {pct}%
        </text>
      </svg>
      <div className="job-text">
        <div className="job-stage">{STAGE_LABELS[job.state] ?? job.state}</div>
        <div className="muted small">{job.state === "failed" ? job.error : job.message}</div>
      </div>
      {running && onCancel && (
        <button type="button" className="btn ghost small" onClick={onCancel}>
          Cancel
        </button>
      )}
    </div>
  );
}
