// Mirrors backend/src/paintreel/models.py and the API responses in api/app.py

export type CropMode = "auto" | "crop" | "pad";

export interface Crop {
  x: number;
  y: number;
  w: number;
  h: number;
  mode: CropMode;
}

export interface Transition {
  type: "cut" | "xfade";
  duration: number;
}

export type SegmentStyle = "opening" | "jump" | "flow" | "moment" | "timelapse" | "hold";

export interface EDLSegment {
  index: number;
  out_start: number;
  out_end: number;
  src_start: number;
  src_end: number;
  mode: "timelapse" | "realtime" | "hold";
  transition_in: Transition;
  zoom: { start: number; end: number } | null;
  curve: [number, number][] | null;
  style: SegmentStyle | null;
  energy: number | null;
  progress_start: number | null;
  progress_end: number | null;
  event: string | null;
}

export interface EDL {
  version: number;
  fps: number;
  width: number;
  height: number;
  song: { path: string; start: number; end: number };
  video: { path: string; duration: number; fps: number; width: number; height: number; keyframe_interval: number };
  crop: Crop;
  segments: EDLSegment[];
  beats: number[];
  downbeats: number[];
  meta: Record<string, unknown>;
}

export interface BadSegment {
  start: number;
  end: number;
  reason: string;
}

export type EventKind = "wash" | "new_color" | "detail" | "bloom" | "change";

export interface VisualEvent {
  time: number;
  start: number;
  end: number;
  before: number;
  after: number;
  strength: number;
  kind: EventKind;
  progress: number;
}

export interface AnalysisSummary {
  duration: number;
  width: number;
  height: number;
  fps: number;
  canvas: Crop;
  canvas_detected: boolean;
  final_time: number;
  complete_time: number;
  start_time: number;
  bad_segments: BadSegment[];
  analysis_seconds: number;
  samples: number;
  events: number;
  event_kinds: Partial<Record<EventKind, number>>;
}

export interface AnalysisCurves extends AnalysisSummary {
  times: number[];
  progress: number[];
  activity: number[];
  occlusion: number[];
  detail: number[];
  colorfulness: number[];
  visual_change: number[];
  events_list: VisualEvent[];
}

export interface SongOverview {
  path: string;
  duration: number;
  tempo: number;
  peaks: number[];
  beats: number[];
  downbeats: number[];
  energy_times: number[];
  energy: number[];
  suggested_start: number;
  suggested_end: number;
}

export interface Asset {
  path: string;
  name: string;
  size: number;
  mtime: number;
  uploaded: boolean;
  duration: number | null;
  analyzed?: boolean;
  final_time?: number | null;
  events?: number | null;
}

export interface PresetInfo {
  name: string;
  title: string;
  icon: string;
  description: string;
}

export interface ControlSpec {
  key: string;
  label: string;
  left: string;
  right: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  defaults: Record<string, number>;
}

export interface PresetsResponse {
  presets: PresetInfo[];
  default: string;
  controls: ControlSpec[];
  max_crop_loss: Record<string, number>;
  zoom_out: Record<string, boolean>;
}

export interface JobParams {
  video: string;
  song: string;
  start: number | null;
  end: number | null;
  preset: string;
  controls: Record<string, number>;
  overrides: Record<string, unknown>;
  crop: Crop | null;
  debug: boolean;
}

export type JobState = "queued" | "analyzing" | "planning" | "rendering" | "done" | "failed" | "cancelled";
export const FINAL_STATES: JobState[] = ["done", "failed", "cancelled"];

export interface Job {
  id: string;
  kind: "analyze" | "render" | "render_edl";
  state: JobState;
  stage: string;
  progress: number;
  message: string;
  created: number;
  updated: number;
  params: Record<string, unknown>;
  outputs: Record<string, string>;
  error: string | null;
  summary: Record<string, unknown>;
}

export interface StoryboardItem {
  index: number;
  out_start: number;
  out_end: number;
  style: SegmentStyle | null;
  mode: string;
  event: string | null;
  transition: "cut" | "xfade";
  src_start: number;
  src_end: number;
  speed: number;
  progress: number | null;
}

export interface PlanPreview {
  edl: EDL;
  storyboard: StoryboardItem[];
  events_total: number;
  summary: Record<string, unknown>;
}

export interface Health {
  version: string;
  ffmpeg: { ok: boolean; ffmpeg: string | null; ffprobe: string | null; error: string | null };
  max_upload_mb: number;
}
