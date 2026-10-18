import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Crop } from "../types";
import { Icon } from "./Icons";

interface Props {
  video: string;
  frameTime: number;
  width: number; // source video size
  height: number;
  detected: Crop;
  crop: Crop | null; // null = detected + automatic fit
  maxCropLoss: number;
  onChange: (crop: Crop | null) => void;
}

type Handle = "move" | "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
const MIN = 40; // source px

/** Same decision as render.resolve_crop on the backend. */
export function fitFor(rect: Crop, maxLoss: number): { mode: "crop" | "pad"; visible: Crop; loss: number } {
  const target = 9 / 16;
  let vw: number, vh: number, loss: number;
  if (rect.w / rect.h > target) {
    vw = rect.h * target;
    vh = rect.h;
    loss = 1 - vw / rect.w;
  } else {
    vw = rect.w;
    vh = rect.w / target;
    loss = 1 - vh / rect.h;
  }
  const mode = rect.mode === "auto" ? (loss <= maxLoss ? "crop" : "pad") : rect.mode;
  const visible =
    mode === "crop"
      ? { x: rect.x + (rect.w - vw) / 2, y: rect.y + (rect.h - vh) / 2, w: vw, h: vh, mode: "crop" as const }
      : { ...rect, mode: "pad" as const };
  return { mode, visible, loss };
}

export function FramingEditor({ video, frameTime, width, height, detected, crop, maxCropLoss, onChange }: Props) {
  const rect = crop ?? { ...detected, mode: "auto" as const };
  const fit = fitFor(rect, maxCropLoss);
  const autoFit = fitFor({ ...rect, mode: "auto" }, maxCropLoss);
  const boxRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const previewRef = useRef<HTMLCanvasElement>(null);
  const drag = useRef<{ handle: Handle; x0: number; y0: number; start: Crop } | null>(null);
  const [imgReady, setImgReady] = useState(false);
  const src = api.frameUrl(video, frameTime, 1280);

  useEffect(() => {
    setImgReady(false);
    const img = new Image();
    img.onload = () => {
      imgRef.current = img;
      setImgReady(true);
    };
    img.src = src;
  }, [src]);

  // 9:16 preview, drawn like the renderer: crop fills the frame, pad = fitted painting over a blurred copy
  useEffect(() => {
    const c = previewRef.current;
    const img = imgRef.current;
    if (!c || !img || !imgReady) return;
    const W = 270;
    const H = 480;
    const dpr = window.devicePixelRatio || 1;
    c.width = W * dpr;
    c.height = H * dpr;
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const sx = img.naturalWidth / width;
    const sy = img.naturalHeight / height;
    const v = fit.visible;
    if (fit.mode === "crop") {
      g.drawImage(img, v.x * sx, v.y * sy, v.w * sx, v.h * sy, 0, 0, W, H);
    } else {
      // blurred cover background
      const cover = Math.max(W / v.w, H / v.h);
      const bw = W / cover;
      const bh = H / cover;
      g.filter = "blur(10px) brightness(0.85)";
      g.drawImage(img, (v.x + (v.w - bw) / 2) * sx, (v.y + (v.h - bh) / 2) * sy, bw * sx, bh * sy, -12, -12, W + 24, H + 24);
      g.filter = "none";
      const scale = Math.min(W / v.w, H / v.h);
      const fw = v.w * scale;
      const fh = v.h * scale;
      g.drawImage(img, v.x * sx, v.y * sy, v.w * sx, v.h * sy, (W - fw) / 2, (H - fh) / 2, fw, fh);
    }
  }, [imgReady, fit.mode, fit.visible.x, fit.visible.y, fit.visible.w, fit.visible.h, width, height]);

  const toSrc = (e: React.PointerEvent) => {
    const r = boxRef.current!.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * width, y: ((e.clientY - r.top) / r.height) * height };
  };

  const begin = (handle: Handle) => (e: React.PointerEvent) => {
    e.stopPropagation();
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    const p = toSrc(e);
    drag.current = { handle, x0: p.x, y0: p.y, start: { ...rect } };
  };

  const move = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const p = toSrc(e);
    const dx = p.x - d.x0;
    const dy = p.y - d.y0;
    let { x, y, w, h } = d.start;
    if (d.handle === "move") {
      x = Math.min(Math.max(0, x + dx), width - w);
      y = Math.min(Math.max(0, y + dy), height - h);
    } else {
      if (d.handle.includes("w")) {
        const nx = Math.min(Math.max(0, x + dx), x + w - MIN);
        w += x - nx;
        x = nx;
      }
      if (d.handle.includes("e")) w = Math.min(Math.max(MIN, w + dx), width - x);
      if (d.handle.includes("n")) {
        const ny = Math.min(Math.max(0, y + dy), y + h - MIN);
        h += y - ny;
        y = ny;
      }
      if (d.handle.includes("s")) h = Math.min(Math.max(MIN, h + dy), height - y);
    }
    onChange({ x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h), mode: rect.mode });
  };

  const end = () => (drag.current = null);
  const pct = (v: number, of: number) => `${(v / of) * 100}%`;
  const handles: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

  return (
    <div className="framing">
      <div className="framing-main">
        <div
          ref={boxRef}
          className="frame-box"
          style={{ aspectRatio: `${width} / ${height}` }}
          onPointerMove={move}
          onPointerUp={end}
          onPointerCancel={end}
        >
          <img src={src} alt="Finished painting" draggable={false} />
          {!imgReady && <div className="frame-loading">Loading frame…</div>}
          <div className="frame-shade" />
          <div
            className="frame-rect"
            style={{ left: pct(rect.x, width), top: pct(rect.y, height), width: pct(rect.w, width), height: pct(rect.h, height) }}
            onPointerDown={begin("move")}
          >
            {handles.map((h) => (
              <span key={h} className={`frame-handle h-${h}`} onPointerDown={begin(h)} />
            ))}
          </div>
          {fit.mode === "crop" && (
            <div
              className="frame-visible"
              style={{ left: pct(fit.visible.x, width), top: pct(fit.visible.y, height), width: pct(fit.visible.w, width), height: pct(fit.visible.h, height) }}
            />
          )}
        </div>
        <div className="muted small framing-help">
          Drag the corners or edges to fit the outline to your paper; drag inside to move it.{" "}
          {fit.mode === "crop" && "The dashed area is what fits in the 9:16 frame."}
        </div>
      </div>
      <div className="framing-side">
        <div className="fit-cards" role="radiogroup" aria-label="How the painting fits the Reel">
          {(["crop", "pad"] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={fit.mode === m}
              className={`fit-card ${fit.mode === m ? "selected" : ""}`}
              onClick={() => onChange({ ...rect, mode: m })}
            >
              <span className={`fit-glyph ${m}`} aria-hidden="true">
                <i />
              </span>
              <span className="fit-title">{m === "crop" ? "Fill the screen" : "Show the whole painting"}</span>
              <span className="muted small">
                {m === "crop" ? `Crops ${Math.round(fit.loss * 100)}% of the paper` : "Blurred background around it"}
                {autoFit.mode === m && " · recommended"}
              </span>
            </button>
          ))}
        </div>
        <div className="phone-preview">
          <canvas ref={previewRef} style={{ width: 270, height: 480 }} aria-label="9:16 preview" />
        </div>
        <button type="button" className="btn ghost small" disabled={!crop} onClick={() => onChange(null)}>
          <Icon name="refresh" size={14} /> Reset to detected paper
        </button>
      </div>
    </div>
  );
}
