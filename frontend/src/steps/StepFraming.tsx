import { FramingEditor } from "../components/FramingEditor";
import type { AnalysisCurves, Crop } from "../types";

interface Props {
  video: string;
  analysis: AnalysisCurves;
  crop: Crop | null;
  maxCropLoss: number;
  onChange: (crop: Crop | null) => void;
}

export function StepFraming(p: Props) {
  return (
    <div className="step-body">
      <header className="step-header">
        <h2>Framing</h2>
        <p className="muted">
          Reels are tall (9:16). Choose whether the painting fills the screen or is shown whole. The outline was placed on your paper automatically
          {p.analysis.canvas_detected ? "" : " — but the edges weren't clear, so please check it"}.
        </p>
      </header>
      <section className="card">
        <FramingEditor
          video={p.video}
          frameTime={p.analysis.final_time}
          width={p.analysis.width}
          height={p.analysis.height}
          detected={p.analysis.canvas}
          crop={p.crop}
          maxCropLoss={p.maxCropLoss}
          onChange={p.onChange}
        />
      </section>
    </div>
  );
}
