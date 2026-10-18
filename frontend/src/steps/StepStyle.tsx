import { Storyboard } from "../components/Storyboard";
import { ControlSliders, PresetCards } from "../components/StyleControls";
import type { PlanPreview, PresetsResponse } from "../types";

interface Props {
  presets: PresetsResponse;
  preset: string;
  controls: Record<string, number>;
  zoomOut: boolean | null;
  debug: boolean;
  video: string;
  plan: PlanPreview | null;
  planLoading: boolean;
  planError: string | null;
  onPreset: (name: string) => void;
  onControls: (c: Record<string, number>) => void;
  onZoomOut: (v: boolean) => void;
  onDebug: (v: boolean) => void;
}

export function StepStyle(p: Props) {
  const zoomDefault = p.presets.zoom_out[p.preset] ?? true;
  return (
    <div className="step-body">
      <header className="step-header">
        <h2>Style</h2>
        <p className="muted">Pick a style and fine-tune it. The storyboard below updates as you change things.</p>
      </header>
      <PresetCards presets={p.presets.presets} value={p.preset} onChange={p.onPreset} />
      <div className="style-grid">
        <section className="card">
          <div className="card-head">
            <h3>Fine-tune</h3>
          </div>
          <ControlSliders controls={p.presets.controls} preset={p.preset} values={p.controls} onChange={p.onControls} />
          <details className="advanced">
            <summary>Advanced</summary>
            <label className="check">
              <input type="checkbox" checked={p.zoomOut ?? zoomDefault} onChange={(e) => p.onZoomOut(e.target.checked)} /> Slow zoom-out on the finished
              painting
            </label>
            <label className="check">
              <input type="checkbox" checked={p.debug} onChange={(e) => p.onDebug(e.target.checked)} /> Also render a debug version (segment info and
              beat markers burned in)
            </label>
          </details>
        </section>
        <section className="card">
          <div className="card-head">
            <h3>Storyboard</h3>
            {p.planLoading && <span className="spinner" aria-label="Updating" />}
          </div>
          <Storyboard video={p.video} plan={p.plan} loading={p.planLoading} error={p.planError} />
        </section>
      </div>
    </div>
  );
}
