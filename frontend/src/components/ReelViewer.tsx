import { useEffect } from "react";
import type { Job } from "../types";
import { Icon } from "./Icons";
import { ResultPlayer } from "./ResultPlayer";

interface Props {
  job: Job;
  onClose: () => void;
  onUseSettings: (job: Job) => void;
}

/** Plays any past reel on top of the current step, without touching the work in progress. */
export function ReelViewer({ job, onClose, onUseSettings }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  return (
    <div className="viewer" role="dialog" aria-modal="true" aria-label="Reel">
      <div className="viewer-backdrop" onClick={onClose} />
      <div className="viewer-panel">
        <button type="button" className="icon-btn viewer-close" onClick={onClose} aria-label="Close">
          <Icon name="x" />
        </button>
        <ResultPlayer job={job} onTryAnother={() => onUseSettings(job)} tryLabel="Use these settings" />
      </div>
    </div>
  );
}
