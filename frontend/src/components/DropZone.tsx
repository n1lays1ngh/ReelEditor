import { useRef, useState } from "react";
import { errorMessage, fmtBytes, uploadFile, type UploadProgress } from "../api";
import { useNotify } from "../state";
import { Icon } from "./Icons";

interface Props {
  kind: "video" | "song";
  title: string;
  hint: string;
  accept: string;
  extensions: string[];
  maxMb: number;
  compact?: boolean;
  onUploaded: (file: { path: string; name: string }) => void;
}

interface Active {
  name: string;
  size: number;
  progress: UploadProgress | null;
  abort: () => void;
}

/** Drag-and-drop (or click) upload area with a live progress card. */
export function DropZone({ kind, title, hint, accept, extensions, maxMb, compact, onUploaded }: Props) {
  const [over, setOver] = useState(false);
  const [active, setActive] = useState<Active | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const depth = useRef(0);
  const notify = useNotify();

  const start = (file: File) => {
    const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!extensions.includes(ext)) {
      notify(`"${file.name}" is not a supported ${kind} file (${extensions.join(", ")}).`, "error");
      return;
    }
    if (file.size > maxMb * 1024 * 1024) {
      notify(`"${file.name}" is larger than the ${maxMb} MB limit.`, "error");
      return;
    }
    const { promise, abort } = uploadFile(kind, file, (p) => setActive((a) => (a ? { ...a, progress: p } : a)));
    setActive({ name: file.name, size: file.size, progress: null, abort });
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    promise
      .then((res) => {
        notify(`Uploaded ${res.name}`, "success");
        onUploaded(res);
      })
      .catch((e) => {
        if ((e as { status?: number }).status !== -1) notify(errorMessage(e), "error");
      })
      .finally(() => {
        window.removeEventListener("beforeunload", warn);
        setActive(null);
      });
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    depth.current = 0;
    setOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) start(file);
  };

  if (active) {
    const p = active.progress;
    const pct = p ? Math.min(100, (p.loaded / Math.max(p.total, 1)) * 100) : 0;
    const eta = p && p.bytesPerSecond > 0 ? Math.max(0, (p.total - p.loaded) / p.bytesPerSecond) : null;
    return (
      <div className={`dropzone uploading ${compact ? "compact" : ""}`} role="status">
        <div className="upload-row">
          <Icon name={kind === "video" ? "film" : "music"} size={22} />
          <div className="upload-info">
            <div className="upload-name">{active.name}</div>
            <div className="muted small">
              {p ? `${fmtBytes(p.loaded)} of ${fmtBytes(p.total)}` : "Starting…"}
              {p && p.bytesPerSecond > 0 && ` · ${fmtBytes(p.bytesPerSecond)}/s`}
              {eta != null && pct < 100 && ` · ${eta < 60 ? `${Math.ceil(eta)} s` : `${Math.ceil(eta / 60)} min`} left`}
              {pct >= 100 && " · finishing…"}
            </div>
          </div>
          <button type="button" className="btn ghost small" onClick={active.abort}>
            Cancel
          </button>
        </div>
        <div className="bar">
          <div className="bar-fill" style={{ width: `${pct}%` }} />
        </div>
      </div>
    );
  }

  return (
    <div
      className={`dropzone ${over ? "over" : ""} ${compact ? "compact" : ""}`}
      role="button"
      tabIndex={0}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && inputRef.current?.click()}
      onDragEnter={(e) => {
        e.preventDefault();
        depth.current += 1;
        setOver(true);
      }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={() => {
        depth.current = Math.max(0, depth.current - 1);
        if (depth.current === 0) setOver(false);
      }}
      onDrop={onDrop}
    >
      <div className="dropzone-icon">
        <Icon name="upload" size={compact ? 20 : 28} />
      </div>
      <div>
        <div className="dropzone-title">{over ? "Drop it!" : title}</div>
        <div className="muted small">{hint}</div>
      </div>
      <input
        ref={inputRef}
        type="file"
        hidden
        accept={accept}
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) start(f);
        }}
      />
    </div>
  );
}
