import type { ControlSpec, PresetInfo } from "../types";
import { Icon } from "./Icons";

export function PresetCards({ presets, value, onChange }: { presets: PresetInfo[]; value: string; onChange: (name: string) => void }) {
  return (
    <div className="preset-grid" role="radiogroup" aria-label="Style">
      {presets.map((p) => (
        <button
          key={p.name}
          type="button"
          role="radio"
          aria-checked={p.name === value}
          className={`preset-card ${p.name === value ? "selected" : ""}`}
          onClick={() => onChange(p.name)}
        >
          <span className={`preset-icon icon-${p.icon}`}>
            <Icon name={p.icon} size={22} />
          </span>
          <span className="preset-title">{p.title}</span>
          <span className="preset-desc">{p.description}</span>
          {p.name === value && (
            <span className="preset-check">
              <Icon name="check" size={14} />
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

interface SliderProps {
  controls: ControlSpec[];
  preset: string;
  values: Record<string, number>;
  onChange: (values: Record<string, number>) => void;
}

/** A few plain-language sliders; each maps to several technical settings on the backend. */
export function ControlSliders({ controls, preset, values, onChange }: SliderProps) {
  return (
    <div className="sliders">
      {controls.map((c) => {
        const def = c.defaults[preset] ?? (c.min + c.max) / 2;
        const changed = c.key in values;
        const v = changed ? values[c.key] : def;
        const pct = ((v - c.min) / (c.max - c.min)) * 100;
        return (
          <div key={c.key} className={`slider ${changed ? "changed" : ""}`}>
            <div className="slider-head">
              <label htmlFor={`ctl-${c.key}`}>{c.label}</label>
              <span className="slider-value">
                {c.unit ? `${v.toFixed(1)} ${c.unit}` : changed ? (v > def ? "more" : v < def ? "less" : "default") : "default"}
              </span>
              <button
                type="button"
                className="icon-btn"
                disabled={!changed}
                aria-label={`Reset ${c.label}`}
                title="Back to the style's default"
                onClick={() => {
                  const next = { ...values };
                  delete next[c.key];
                  onChange(next);
                }}
              >
                <Icon name="refresh" size={14} />
              </button>
            </div>
            <input
              id={`ctl-${c.key}`}
              type="range"
              min={c.min}
              max={c.max}
              step={c.step}
              value={v}
              style={{ "--pct": `${pct}%` } as React.CSSProperties}
              onChange={(e) => onChange({ ...values, [c.key]: Number(e.target.value) })}
            />
            {(c.left || c.right) && (
              <div className="slider-ends muted small">
                <span>{c.left}</span>
                <span>{c.right}</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
