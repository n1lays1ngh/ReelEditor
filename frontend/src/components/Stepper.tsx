import { Icon } from "./Icons";

export interface StepDef {
  title: string;
  icon: string;
  done: boolean;
  enabled: boolean;
}

interface Props {
  steps: StepDef[];
  current: number;
  onSelect: (index: number) => void;
}

export function Stepper({ steps, current, onSelect }: Props) {
  return (
    <nav className="stepper" aria-label="Steps">
      <ol>
        {steps.map((s, i) => {
          const state = i === current ? "current" : s.done ? "done" : s.enabled ? "open" : "locked";
          return (
            <li key={s.title} className={`step ${state}`}>
              <button
                type="button"
                disabled={!s.enabled}
                aria-current={i === current ? "step" : undefined}
                onClick={() => onSelect(i)}
              >
                <span className="step-dot">{s.done && i !== current ? <Icon name="check" size={14} /> : i + 1}</span>
                <span className="step-title">{s.title}</span>
              </button>
              {i < steps.length - 1 && <span className="step-line" aria-hidden="true" />}
            </li>
          );
        })}
      </ol>
      <div className="stepper-compact" aria-hidden="true">
        Step {current + 1} of {steps.length} · <strong>{steps[current]?.title}</strong>
      </div>
    </nav>
  );
}
