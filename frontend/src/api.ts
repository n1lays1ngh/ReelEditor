import type {
  AnalysisCurves,
  Asset,
  Health,
  Job,
  JobParams,
  PlanPreview,
  PresetsResponse,
  SongOverview,
} from "./types";
import { FINAL_STATES } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new ApiError(0, "Can't reach the PaintReel server. Is it running?");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const post = <T>(url: string, body: unknown) =>
  req<T>(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const q = (params: Record<string, string | number | boolean>) =>
  new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString();

export const api = {
  health: () => req<Health>("/api/health"),
  presets: () => req<PresetsResponse>("/api/presets"),
  assets: (kind: "video" | "song") => req<Asset[]>(`/api/assets?${q({ kind })}`),
  deleteAsset: (path: string) => req<{ deleted: string }>(`/api/assets?${q({ path })}`, { method: "DELETE" }),
  songOverview: (path: string) => post<SongOverview>("/api/songs/overview", { path }),
  analysis: (path: string) => req<AnalysisCurves | null>(`/api/video/analysis?${q({ path })}`),
  plan: (p: JobParams) => post<PlanPreview>("/api/plan", p),
  analyze: (video: string) => post<Job>("/api/jobs/analyze", { video }),
  render: (p: JobParams) => post<Job>("/api/jobs/render", p),
  cancel: (id: string) => post<Job>(`/api/jobs/${id}/cancel`, {}),
  job: (id: string) => req<Job>(`/api/jobs/${id}`),
  jobs: () => req<Job[]>("/api/jobs"),
  renders: () => req<Job[]>("/api/renders"),
  deleteRender: (id: string) => req<{ deleted: string }>(`/api/renders/${id}`, { method: "DELETE" }),

  frameUrl: (path: string, t: number, width = 640) => `/api/video/frame?${q({ path, t: t.toFixed(2), width })}`,
  thumbUrl: (path: string, t: number, clean = true, width = 240) =>
    `/api/video/thumb?${q({ path, t: t.toFixed(2), clean, width })}`,
  mediaUrl: (path: string) => `/api/media?${q({ path })}`,
  fileUrl: (job: Job, name: string) => `/api/jobs/${job.id}/files/${encodeURIComponent(name)}`,
};

export interface UploadProgress {
  loaded: number;
  total: number;
  bytesPerSecond: number;
}

/** Upload with progress (fetch can't report upload progress). Returns the stored path and an abort function. */
export function uploadFile(
  kind: "video" | "song",
  file: File,
  onProgress: (p: UploadProgress) => void,
): { promise: Promise<{ path: string; name: string }>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const started = performance.now();
  const promise = new Promise<{ path: string; name: string }>((resolve, reject) => {
    xhr.upload.onprogress = (e) => {
      const secs = Math.max(0.001, (performance.now() - started) / 1000);
      onProgress({ loaded: e.loaded, total: e.lengthComputable ? e.total : file.size, bytesPerSecond: e.loaded / secs });
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        let msg = xhr.statusText || `upload failed (${xhr.status})`;
        try {
          msg = JSON.parse(xhr.responseText).detail ?? msg;
        } catch {
          /* not json */
        }
        reject(new ApiError(xhr.status, msg));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "Upload failed: the connection was lost."));
    xhr.onabort = () => reject(new ApiError(-1, "Upload cancelled."));
    const fd = new FormData();
    fd.append("kind", kind);
    fd.append("file", file);
    xhr.open("POST", "/api/upload");
    xhr.send(fd);
  });
  return { promise, abort: () => xhr.abort() };
}

/** Follow a job over Server-Sent Events until it finishes. Reconnects when the tab comes back. */
export function followJob(id: string, onUpdate: (job: Job) => void): () => void {
  let es: EventSource | null = null;
  let closed = false;
  let finished = false;

  const handle = (job: Job) => {
    onUpdate(job);
    if (FINAL_STATES.includes(job.state)) {
      finished = true;
      es?.close();
    }
  };
  const connect = () => {
    if (closed || finished) return;
    es?.close();
    es = new EventSource(`/api/jobs/${id}/events`);
    es.addEventListener("job", (ev) => handle(JSON.parse((ev as MessageEvent).data) as Job));
    es.onerror = () => {
      es?.close();
      // Poll once, then retry the stream shortly (server restarts, sleeping laptops, mobile tabs).
      api.job(id).then(handle).catch(() => undefined);
      if (!closed && !finished) window.setTimeout(connect, 2000);
    };
  };
  const onVisible = () => {
    if (document.visibilityState === "visible") connect();
  };
  document.addEventListener("visibilitychange", onVisible);
  connect();
  return () => {
    closed = true;
    es?.close();
    document.removeEventListener("visibilitychange", onVisible);
  };
}

// ------------------------------------------------------------------ formatting helpers

export function fmtTime(t: number, decimals = 1): string {
  const sign = t < 0 ? "-" : "";
  t = Math.abs(t);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t - h * 3600 - m * 60;
  const ss = s.toFixed(decimals).padStart(decimals ? 3 + decimals : 2, "0");
  return h ? `${sign}${h}:${String(m).padStart(2, "0")}:${ss}` : `${sign}${m}:${ss}`;
}

export function fmtDuration(t: number | null | undefined): string {
  if (t == null) return "–";
  if (t >= 3600) return `${Math.floor(t / 3600)} h ${Math.round((t % 3600) / 60)} min`;
  if (t >= 60) return `${Math.floor(t / 60)} min ${Math.round(t % 60)} s`;
  return `${t.toFixed(1)} s`;
}

export function fmtBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e8 ? 0 : 1)} MB`;
  return `${Math.max(1, Math.round(n / 1e3))} KB`;
}

export function stem(path: string): string {
  const name = path.split("/").pop() ?? path;
  return name.replace(/\.[^.]+$/, "");
}

export function errorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
