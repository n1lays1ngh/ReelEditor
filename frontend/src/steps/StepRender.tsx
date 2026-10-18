import { fmtTime } from "../api";
import { Icon } from "../components/Icons";
import { JobProgress } from "../components/JobProgress";
import { ResultPlayer } from "../components/ResultPlayer";
import type { Project } from "../state";
import type { Job, PresetsResponse } from "../types";

interface Props {
  project: Project;
  presets: PresetsResponse;
  renderJob: Job | null;
  result: Job | null;
  canRender: boolean;
  onRender: () => void;
  onCancel: () => void;
  onGoto: (step: number) => void;
}

export function StepRender(p: Props) {
  const running = p.renderJob && !["done", "failed", "cancelled"].includes(p.renderJob.state);
  const preset = p.presets.presets.find((x) => x.name === p.project.preset);
  const changed = p.presets.controls.filter((c) => c.key in p.project.controls);
  const seg = p.project.segment;
  const rows: { label: string; value: string; step: number }[] = [
    { label: "Painting", value: p.project.video?.name ?? "–", step: 0 },
    {
      label: "Music",
      value: `${p.project.song?.name ?? "–"}${seg ? ` · ${fmtTime(seg[0])} → ${fmtTime(seg[1])} (${(seg[1] - seg[0]).toFixed(1)} s)` : ""}`,
      step: 1,
    },
    {
      label: "Style",
      value: `${preset?.title ?? p.project.preset}${changed.length ? ` · adjusted ${changed.map((c) => c.label.toLowerCase()).join(", ")}` : ""}`,
      step: 2,
    },
    {
      label: "Framing",
      value: p.project.crop
        ? `${p.project.crop.mode === "pad" ? "Whole painting" : p.project.crop.mode === "crop" ? "Fill the screen" : "Automatic"} · custom outline`
        : "Automatic",
      step: 3,
    },
  ];

  return (
    <div className="step-body">
      <header className="step-header">
        <h2>Render & download</h2>
        <p className="muted">Check your choices and render. You can keep trying styles — the painting is only analyzed once.</p>
      </header>
      <section className="card summary-card">
        <dl className="summary">
          {rows.map((r) => (
            <div key={r.label} className="summary-row">
              <dt>{r.label}</dt>
              <dd>{r.value}</dd>
              <button type="button" className="btn ghost small" onClick={() => p.onGoto(r.step)}>
                Edit
              </button>
            </div>
          ))}
        </dl>
        <div className="render-row">
          {running && p.renderJob ? (
            <JobProgress job={p.renderJob} onCancel={p.onCancel} />
          ) : (
            <button type="button" className="btn primary big" disabled={!p.canRender} onClick={p.onRender}>
              <Icon name="film" /> {p.result ? "Render again" : "Render reel"}
            </button>
          )}
          {p.renderJob && ["failed", "cancelled"].includes(p.renderJob.state) && !running && <JobProgress job={p.renderJob} />}
        </div>
      </section>
      {p.result && <ResultPlayer job={p.result} onTryAnother={() => p.onGoto(2)} />}
    </div>
  );
}
