import { useEffect, useState } from "react";
import { api, errorMessage, fmtTime, stem } from "../api";
import { useNotify } from "../state";
import type { Job } from "../types";
import { Icon } from "./Icons";
import { downloadName } from "./ResultPlayer";

interface Props {
  open: boolean;
  reels: Job[];
  presetTitles: Record<string, string>;
  onClose: () => void;
  onPlay: (job: Job) => void;
  onOpenSettings: (job: Job) => void;
  onChanged: () => void;
}

/** Side drawer with previous renders. */
export function ReelsDrawer({ open, reels, presetTitles, onClose, onPlay, onOpenSettings, onChanged }: Props) {
  const notify = useNotify();
  const [confirming, setConfirming] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const remove = async (job: Job) => {
    try {
      await api.deleteRender(job.id);
      onChanged();
    } catch (e) {
      notify(errorMessage(e), "error");
    } finally {
      setConfirming(null);
    }
  };

  return (
    <>
      <div className={`drawer-backdrop ${open ? "open" : ""}`} onClick={onClose} aria-hidden="true" />
      <aside className={`drawer ${open ? "open" : ""}`} aria-label="My reels" aria-hidden={!open}>
        <div className="drawer-head">
          <h2>My reels</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </div>
        {!reels.length && <div className="muted drawer-empty">Your rendered reels will appear here.</div>}
        <div className="reel-list">
          {reels.map((j) => {
            const p = j.params as Record<string, unknown>;
            const s = j.summary as Record<string, unknown>;
            const done = j.state === "done";
            return (
              <div key={j.id} className={`reel-card ${j.state}`}>
                <button type="button" className="reel-thumb" disabled={!done} onClick={() => done && onPlay(j)} aria-label="Play">
                  {done && j.outputs.cover ? (
                    <img src={api.fileUrl(j, j.outputs.cover)} alt="" loading="lazy" />
                  ) : (
                    <span className="reel-thumb-empty">
                      <Icon name={j.state === "failed" ? "x" : "clock"} />
                    </span>
                  )}
                  {done && (
                    <span className="reel-play">
                      <Icon name="play" size={16} />
                    </span>
                  )}
                </button>
                <div className="reel-body">
                  <div className="reel-title">
                    {j.kind === "render_edl" ? "Edited plan" : presetTitles[String(p.preset)] ?? String(p.preset)}
                    <span className={`badge ${j.state}`}>{done ? `${Number(s.duration ?? 0).toFixed(0)} s` : j.state}</span>
                  </div>
                  <div className="muted small ellipsis">
                    {stem(String(p.video ?? ""))} · {stem(String(p.song ?? ""))}
                  </div>
                  <div className="muted small">
                    {new Date(j.created * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
                    {p.start != null && ` · song ${fmtTime(Number(p.start), 0)}`}
                  </div>
                  {j.error && <div className="error-text small ellipsis" title={j.error}>{j.error}</div>}
                  <div className="reel-actions">
                    {done && (
                      <a className="icon-btn" href={api.fileUrl(j, j.outputs.reel)} download={downloadName(j, false)} aria-label="Download">
                        <Icon name="download" size={16} />
                      </a>
                    )}
                    {j.kind === "render" && (
                      <button type="button" className="btn ghost small" onClick={() => onOpenSettings(j)}>
                        <Icon name="settings" size={14} /> Use settings
                      </button>
                    )}
                    {confirming === j.id ? (
                      <>
                        <button type="button" className="btn danger small" onClick={() => remove(j)}>
                          Delete
                        </button>
                        <button type="button" className="btn ghost small" onClick={() => setConfirming(null)}>
                          Keep
                        </button>
                      </>
                    ) : (
                      <button type="button" className="icon-btn" aria-label="Delete" onClick={() => setConfirming(j.id)}>
                        <Icon name="trash" size={16} />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </aside>
    </>
  );
}
