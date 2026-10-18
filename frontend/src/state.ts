import { createContext, useContext } from "react";
import type { Crop, JobParams } from "./types";

/** Everything the user chose. Persisted in localStorage so a refresh keeps the work. */
export interface Project {
  video: { path: string; name: string } | null;
  song: { path: string; name: string } | null;
  segment: [number, number] | null;
  preset: string;
  controls: Record<string, number>; // only controls the user moved
  crop: Crop | null; // null = detected canvas, automatic fit
  zoomOut: boolean | null; // null = preset default
  debug: boolean;
}

export const EMPTY_PROJECT: Project = {
  video: null,
  song: null,
  segment: null,
  preset: "energetic",
  controls: {},
  crop: null,
  zoomOut: null,
  debug: false,
};

const KEY = "paintreel.project.v2";

export function loadProject(): Project {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return EMPTY_PROJECT;
    return { ...EMPTY_PROJECT, ...(JSON.parse(raw) as Partial<Project>) };
  } catch {
    return EMPTY_PROJECT;
  }
}

export function saveProject(p: Project): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* storage unavailable (private mode): the app still works, it just won't remember */
  }
}

export function jobParams(p: Project): JobParams | null {
  if (!p.video || !p.song) return null;
  return {
    video: p.video.path,
    song: p.song.path,
    start: p.segment?.[0] ?? null,
    end: p.segment?.[1] ?? null,
    preset: p.preset,
    controls: p.controls,
    overrides: p.zoomOut === null ? {} : { planner: { ending: { zoom_out: p.zoomOut } } },
    crop: p.crop,
    debug: p.debug,
  };
}

/** Restore a project from a past render's parameters. */
export function projectFromParams(params: Record<string, unknown>, current: Project): Project {
  const pathOf = (v: unknown) => (typeof v === "string" ? { path: v, name: v.split("/").pop() ?? v } : null);
  const overrides = (params.overrides ?? {}) as { planner?: { ending?: { zoom_out?: boolean } } };
  const start = params.start as number | null | undefined;
  const end = params.end as number | null | undefined;
  return {
    ...current,
    video: pathOf(params.video) ?? current.video,
    song: pathOf(params.song) ?? current.song,
    segment: start != null && end != null ? [start, end] : null,
    preset: typeof params.preset === "string" ? params.preset : current.preset,
    controls: (params.controls as Record<string, number>) ?? {},
    crop: (params.crop as Crop | null) ?? null,
    zoomOut: overrides.planner?.ending?.zoom_out ?? null,
    debug: Boolean(params.debug),
  };
}

// ------------------------------------------------------------------ notifications

export type ToastKind = "info" | "success" | "error";
export interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
}
export type Notify = (text: string, kind?: ToastKind) => void;

export const NotifyContext = createContext<Notify>(() => undefined);
export const useNotify = () => useContext(NotifyContext);
