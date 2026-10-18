import { useState } from "react";
import { api, errorMessage, fmtBytes, fmtDuration } from "../api";
import { useNotify } from "../state";
import type { Asset } from "../types";
import { Icon } from "./Icons";

interface Props {
  kind: "video" | "song";
  assets: Asset[];
  selected: string | null;
  loading: boolean;
  onSelect: (a: Asset) => void;
  onDeleted: (path: string) => void;
}

/** Previously uploaded files as cards: select to reuse, delete to free space. */
export function AssetGrid({ kind, assets, selected, loading, onSelect, onDeleted }: Props) {
  const notify = useNotify();
  const [confirming, setConfirming] = useState<string | null>(null);

  if (loading && !assets.length) {
    return (
      <div className="asset-grid">
        {[0, 1, 2].map((i) => (
          <div key={i} className="asset-card skeleton" />
        ))}
      </div>
    );
  }
  if (!assets.length) return null;

  const remove = async (a: Asset) => {
    try {
      await api.deleteAsset(a.path);
      notify(`Deleted ${a.name}`, "info");
      onDeleted(a.path);
    } catch (e) {
      notify(errorMessage(e), "error");
    } finally {
      setConfirming(null);
    }
  };

  return (
    <div className="asset-grid" role="list">
      {assets.map((a) => {
        const isSel = a.path === selected;
        const thumb =
          kind === "video"
            ? a.analyzed && a.final_time != null
              ? api.thumbUrl(a.path, a.final_time, false, 320)
              : api.frameUrl(a.path, Math.min(5, (a.duration ?? 10) / 2), 320)
            : null;
        return (
          <div
            key={a.path}
            role="listitem"
            className={`asset-card ${isSel ? "selected" : ""} ${kind}`}
            onClick={() => onSelect(a)}
            onKeyDown={(e) => e.key === "Enter" && onSelect(a)}
            tabIndex={0}
            title={a.path}
          >
            <div className="asset-thumb">
              {thumb ? (
                <img src={thumb} alt="" loading="lazy" draggable={false} />
              ) : (
                <div className="asset-icon">
                  <Icon name="music" size={28} />
                </div>
              )}
              {isSel && (
                <span className="asset-check">
                  <Icon name="check" size={14} />
                </span>
              )}
              {kind === "video" && a.analyzed && <span className="asset-badge">ready</span>}
            </div>
            <div className="asset-meta">
              <div className="asset-name">{a.name}</div>
              <div className="muted small">
                {fmtDuration(a.duration)} · {fmtBytes(a.size)}
                {kind === "video" && a.events != null && ` · ${a.events} moments`}
              </div>
            </div>
            {a.uploaded &&
              (confirming === a.path ? (
                <div className="asset-confirm" onClick={(e) => e.stopPropagation()}>
                  <button type="button" className="btn danger small" onClick={() => remove(a)}>
                    Delete
                  </button>
                  <button type="button" className="btn ghost small" onClick={() => setConfirming(null)}>
                    Keep
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  className="icon-btn asset-delete"
                  aria-label={`Delete ${a.name}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirming(a.path);
                  }}
                >
                  <Icon name="trash" size={15} />
                </button>
              ))}
          </div>
        );
      })}
    </div>
  );
}
