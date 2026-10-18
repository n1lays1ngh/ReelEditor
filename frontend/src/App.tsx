import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, errorMessage, followJob } from "./api";
import { Icon } from "./components/Icons";
import { ReelsDrawer } from "./components/ReelsDrawer";
import { ReelViewer } from "./components/ReelViewer";
import { Stepper, type StepDef } from "./components/Stepper";
import { Toasts } from "./components/Toasts";
import {
  jobParams,
  loadProject,
  NotifyContext,
  projectFromParams,
  saveProject,
  type Project,
  type Toast,
  type ToastKind,
} from "./state";
import { StepFraming } from "./steps/StepFraming";
import { StepMusic } from "./steps/StepMusic";
import { StepPainting } from "./steps/StepPainting";
import { StepRender } from "./steps/StepRender";
import { StepStyle } from "./steps/StepStyle";
import type { AnalysisCurves, Asset, Health, Job, PlanPreview, PresetsResponse, SongOverview } from "./types";
import { FINAL_STATES } from "./types";

const STEP_KEY = "paintreel.step";
const RESULT_KEY = "paintreel.result";
const isFinal = (j: Job | null) => !j || FINAL_STATES.includes(j.state);

function readSavedResult(): string | null {
  try {
    return window.localStorage.getItem(RESULT_KEY);
  } catch {
    return null;
  }
}

function loadStep(): number {
  try {
    return Math.min(4, Math.max(0, Number(window.localStorage.getItem(STEP_KEY) ?? 0) || 0));
  } catch {
    return 0;
  }
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [presets, setPresets] = useState<PresetsResponse | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [project, setProjectState] = useState<Project>(loadProject);
  const [step, setStepState] = useState<number>(loadStep);
  const [visited, setVisited] = useState<number>(loadStep);
  const [videos, setVideos] = useState<Asset[]>([]);
  const [songs, setSongs] = useState<Asset[]>([]);
  const [assetsLoading, setAssetsLoading] = useState(true);
  const [analysis, setAnalysis] = useState<AnalysisCurves | null>(null);
  const [analysisJob, setAnalysisJob] = useState<Job | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [overview, setOverview] = useState<SongOverview | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [plan, setPlan] = useState<PlanPreview | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [renderJob, setRenderJob] = useState<Job | null>(null);
  const [result, setResult] = useState<Job | null>(null);
  const [reels, setReels] = useState<Job[]>([]);
  const [drawer, setDrawer] = useState(false);
  const [viewing, setViewing] = useState<Job | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const videoRef = useRef<string | null>(project.video?.path ?? null);
  const followers = useRef<Record<string, () => void>>({});
  const planSeq = useRef(0);
  const toastSeq = useRef(0);
  const savedResultId = useRef<string | null>(readSavedResult()); // read before any effect can overwrite it

  // ------------------------------------------------------------------ helpers

  const setProject = useCallback((fn: (p: Project) => Project) => {
    setProjectState((prev) => {
      const next = fn(prev);
      saveProject(next);
      return next;
    });
  }, []);

  const setStep = useCallback((s: number) => {
    setStepState(s);
    setVisited((v) => Math.max(v, s));
    try {
      window.localStorage.setItem(STEP_KEY, String(s));
    } catch {
      /* ignore */
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const notify = useCallback(
    (text: string, kind: ToastKind = "info") => {
      const id = ++toastSeq.current;
      setToasts((t) => [...t.slice(-3), { id, kind, text }]);
      window.setTimeout(() => dismiss(id), kind === "error" ? 9000 : 4500);
    },
    [dismiss],
  );

  const follow = useCallback((job: Job, onUpdate: (j: Job) => void) => {
    followers.current[job.id]?.();
    followers.current[job.id] = followJob(job.id, (j) => {
      onUpdate(j);
      if (FINAL_STATES.includes(j.state)) {
        followers.current[job.id]?.();
        delete followers.current[job.id];
      }
    });
  }, []);

  useEffect(() => () => Object.values(followers.current).forEach((stop) => stop()), []);

  const refreshAssets = useCallback(async (kind?: "video" | "song") => {
    try {
      if (!kind || kind === "video") setVideos(await api.assets("video"));
      if (!kind || kind === "song") setSongs(await api.assets("song"));
    } catch (e) {
      setBootError(errorMessage(e));
    } finally {
      setAssetsLoading(false);
    }
  }, []);

  const refreshReels = useCallback(() => api.renders().then(setReels).catch(() => undefined), []);

  // ------------------------------------------------------------------ boot

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setBootError(errorMessage(e)));
    api
      .presets()
      .then((p) => {
        setPresets(p);
        setProject((cur) => (p.presets.some((x) => x.name === cur.preset) ? cur : { ...cur, preset: p.default }));
      })
      .catch((e) => setBootError(errorMessage(e)));
    refreshAssets();
    refreshReels();
  }, [refreshAssets, refreshReels, setProject]);

  // ------------------------------------------------------------------ analysis

  const onAnalysisUpdate = useCallback(
    (path: string) => (j: Job) => {
      if (videoRef.current !== path) return;
      setAnalysisJob(j);
      if (j.state === "done") {
        api
          .analysis(path)
          .then((a) => {
            if (videoRef.current === path && a) setAnalysis(a);
          })
          .catch((e) => setAnalysisError(errorMessage(e)));
        refreshAssets("video");
        notify("Your painting is analyzed.", "success");
      } else if (j.state === "failed") {
        setAnalysisError(j.error ?? "Analysis failed.");
      }
    },
    [notify, refreshAssets],
  );

  const startAnalysis = useCallback(
    async (path: string) => {
      setAnalysisError(null);
      try {
        const job = await api.analyze(path);
        if (videoRef.current !== path) return;
        setAnalysisJob(job);
        follow(job, onAnalysisUpdate(path));
      } catch (e) {
        setAnalysisError(errorMessage(e));
      }
    },
    [follow, onAnalysisUpdate],
  );

  useEffect(() => {
    const path = project.video?.path ?? null;
    videoRef.current = path;
    setAnalysis(null);
    setAnalysisJob(null);
    setAnalysisError(null);
    setPlan(null);
    if (!path) return;
    api
      .analysis(path)
      .then((a) => {
        if (videoRef.current !== path) return;
        if (a) setAnalysis(a);
        else startAnalysis(path); // not analyzed yet
      })
      .catch((e) => {
        if (videoRef.current !== path) return;
        if (e instanceof ApiError && (e.status === 400 || e.status === 403)) {
          notify("That video is no longer available.", "error");
          setProject((p) => ({ ...p, video: null, crop: null }));
        } else setAnalysisError(errorMessage(e));
      });
  }, [project.video?.path, startAnalysis, notify, setProject]);

  const cancelAnalysis = () => {
    if (analysisJob) api.cancel(analysisJob.id).catch((e) => notify(errorMessage(e), "error"));
  };

  // ------------------------------------------------------------------ song

  useEffect(() => {
    const path = project.song?.path ?? null;
    setOverview(null);
    if (!path) return;
    setOverviewLoading(true);
    api
      .songOverview(path)
      .then((ov) => {
        setOverview(ov);
        setProject((p) => {
          const s = p.segment;
          const valid = s && s[0] >= 0 && s[1] <= ov.duration + 0.01 && s[1] - s[0] >= 1;
          return valid ? p : { ...p, segment: [ov.suggested_start, ov.suggested_end] };
        });
      })
      .catch((e) => {
        notify(errorMessage(e), "error");
        if (e instanceof ApiError && (e.status === 400 || e.status === 403)) setProject((p) => ({ ...p, song: null, segment: null }));
      })
      .finally(() => setOverviewLoading(false));
  }, [project.song?.path, notify, setProject]);

  // ------------------------------------------------------------------ resume running jobs after a refresh

  useEffect(() => {
    api
      .jobs()
      .then((jobs) => {
        const active = jobs.filter((j) => !FINAL_STATES.includes(j.state));
        const render = active.find((j) => j.kind === "render");
        if (render) {
          setRenderJob(render);
          follow(render, onRenderUpdate);
        }
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------------ plan preview (storyboard)

  const params = useMemo(() => jobParams(project), [project]);
  const ready0 = Boolean(project.video && analysis);
  const ready1 = Boolean(project.song && overview && project.segment);

  useEffect(() => {
    if (step < 2 || !ready0 || !ready1 || !params) return;
    const seq = ++planSeq.current;
    setPlanLoading(true);
    const timer = window.setTimeout(() => {
      api
        .plan(params)
        .then((p) => {
          if (seq !== planSeq.current) return;
          setPlan(p);
          setPlanError(null);
        })
        .catch((e) => seq === planSeq.current && setPlanError(errorMessage(e)))
        .finally(() => seq === planSeq.current && setPlanLoading(false));
    }, 450);
    return () => window.clearTimeout(timer);
  }, [step, ready0, ready1, params]);

  // ------------------------------------------------------------------ render

  const onRenderUpdate = useCallback(
    (j: Job) => {
      setRenderJob(j);
      if (j.state === "done") {
        setResult(j);
        refreshReels();
        notify("Your reel is ready!", "success");
      } else if (j.state === "failed") {
        notify(j.error ?? "Rendering failed.", "error");
        refreshReels();
      } else if (j.state === "cancelled") {
        notify("Rendering cancelled.", "info");
        refreshReels();
      }
    },
    [notify, refreshReels],
  );

  const startRender = async () => {
    if (!params) return;
    try {
      const job = await api.render(params);
      setRenderJob(job);
      follow(job, onRenderUpdate);
      refreshReels();
    } catch (e) {
      notify(errorMessage(e), "error");
    }
  };

  const cancelRender = () => {
    if (renderJob) api.cancel(renderJob.id).catch((e) => notify(errorMessage(e), "error"));
  };

  // a deleted reel can't stay on screen
  useEffect(() => {
    setResult((r) => (r && !reels.some((x) => x.id === r.id) ? null : r));
    setViewing((v) => (v && !reels.some((x) => x.id === v.id) ? null : v));
  }, [reels]);

  // remember the reel on screen so a refresh shows it again
  const restoredOnce = useRef(false);
  useEffect(() => {
    if (!result && !restoredOnce.current) {
      restoredOnce.current = true; // first render: nothing to save yet, keep the stored id for the restore below
      return;
    }
    try {
      if (result) window.localStorage.setItem(RESULT_KEY, result.id);
      else window.localStorage.removeItem(RESULT_KEY);
    } catch {
      /* ignore */
    }
  }, [result]);
  useEffect(() => {
    const id = savedResultId.current;
    if (id) api.job(id).then((j) => j.state === "done" && setResult((r) => r ?? j)).catch(() => undefined);
  }, []);

  // ------------------------------------------------------------------ steps

  const presetTitles = useMemo(
    () => Object.fromEntries((presets?.presets ?? []).map((p) => [p.name, p.title])) as Record<string, string>,
    [presets],
  );
  const steps: StepDef[] = [
    { title: "Painting", icon: "film", done: ready0, enabled: true },
    { title: "Music", icon: "music", done: ready1, enabled: Boolean(project.video) },
    { title: "Style", icon: "palette", done: ready0 && ready1 && visited > 2, enabled: ready0 && ready1 },
    { title: "Framing", icon: "crop", done: ready0 && ready1 && visited > 3, enabled: ready0 && ready1 },
    { title: "Render", icon: "download", done: Boolean(result), enabled: ready0 && ready1 },
  ];
  const canNext = step < steps.length - 1 && steps[step + 1].enabled;
  const nextHint =
    step === 0 && !project.video
      ? "Upload or pick a painting video"
      : step === 0 && !analysis
        ? "Analyzing… you can already pick the music"
        : step === 1 && !project.song
          ? "Upload or pick a song"
          : step === 1 && !ready0
            ? "Waiting for the painting analysis to finish"
            : "";
  const holdSeconds =
    project.controls.hold ?? presets?.controls.find((c) => c.key === "hold")?.defaults[project.preset] ?? 2.5;
  const segLen = project.segment ? project.segment[1] - project.segment[0] : null;

  function loadSettings(j: Job) {
    setProject((p) => projectFromParams(j.params, p));
    setResult(j);
    setDrawer(false);
    setViewing(null);
    setStep(2);
    notify("Settings loaded from that reel.", "info");
  }

  const selectVideo = (a: { path: string; name: string }) => {
    setProject((p) => (p.video?.path === a.path ? p : { ...p, video: a, crop: null }));
    refreshAssets("video");
  };
  const selectSong = (a: { path: string; name: string }) => {
    setProject((p) => (p.song?.path === a.path ? p : { ...p, song: a, segment: null }));
    refreshAssets("song");
  };
  const assetDeleted = (kind: "video" | "song") => (path: string) => {
    if (kind === "video" && project.video?.path === path) setProject((p) => ({ ...p, video: null, crop: null }));
    if (kind === "song" && project.song?.path === path) setProject((p) => ({ ...p, song: null, segment: null }));
    refreshAssets(kind);
  };

  // a step whose inputs aren't ready (e.g. right after a refresh) falls back to the last available one
  let shownStep = step;
  while (shownStep > 0 && !steps[shownStep]?.enabled) shownStep -= 1;

  return (
    <NotifyContext.Provider value={notify}>
      <div className="app">
        <header className="topbar">
          <div className="brand">
            <span className="logo" aria-hidden="true">
              <Icon name="brush" size={20} />
            </span>
            <div>
              <div className="brand-name">PaintReel</div>
              <div className="brand-sub">Painting videos → beat-synced Reels</div>
            </div>
          </div>
          <span className="grow" />
          <button type="button" className="btn ghost" onClick={() => setDrawer(true)}>
            <Icon name="reels" /> My reels {reels.length > 0 && <span className="count">{reels.length}</span>}
          </button>
        </header>
        {health && !health.ffmpeg.ok && <div className="banner error">⚠ {health.ffmpeg.error}</div>}
        {bootError && <div className="banner error">⚠ {bootError}</div>}

        <Stepper steps={steps} current={shownStep} onSelect={setStep} />

        <main className="main">
          {!presets ? (
            <div className="boot">{bootError ? "Can't load PaintReel." : "Loading…"}</div>
          ) : (
            <>
              {shownStep === 0 && (
                <StepPainting
                  video={project.video}
                  assets={videos}
                  assetsLoading={assetsLoading}
                  maxMb={health?.max_upload_mb ?? 4000}
                  analysis={analysis}
                  analysisJob={analysisJob}
                  analysisError={analysisError}
                  onSelect={selectVideo}
                  onDeleted={assetDeleted("video")}
                  onRetry={() => project.video && startAnalysis(project.video.path)}
                  onCancelAnalysis={cancelAnalysis}
                />
              )}
              {shownStep === 1 && (
                <StepMusic
                  song={project.song}
                  assets={songs}
                  assetsLoading={assetsLoading}
                  maxMb={health?.max_upload_mb ?? 4000}
                  overview={overview}
                  overviewLoading={overviewLoading}
                  segment={project.segment}
                  holdSeconds={holdSeconds}
                  onSelect={selectSong}
                  onDeleted={assetDeleted("song")}
                  onSegment={(s, e) => setProject((p) => ({ ...p, segment: [s, e] }))}
                />
              )}
              {shownStep === 2 && project.video && (
                <StepStyle
                  presets={presets}
                  preset={project.preset}
                  controls={project.controls}
                  zoomOut={project.zoomOut}
                  debug={project.debug}
                  video={project.video.path}
                  plan={plan}
                  planLoading={planLoading}
                  planError={planError}
                  onPreset={(name) => setProject((p) => ({ ...p, preset: name }))}
                  onControls={(c) => setProject((p) => ({ ...p, controls: c }))}
                  onZoomOut={(v) => setProject((p) => ({ ...p, zoomOut: v }))}
                  onDebug={(v) => setProject((p) => ({ ...p, debug: v }))}
                />
              )}
              {shownStep === 3 && project.video && analysis && (
                <StepFraming
                  video={project.video.path}
                  analysis={analysis}
                  crop={project.crop}
                  maxCropLoss={presets.max_crop_loss[project.preset] ?? 0.2}
                  onChange={(crop) => setProject((p) => ({ ...p, crop }))}
                />
              )}
              {shownStep === 4 && (
                <StepRender
                  project={project}
                  presets={presets}
                  renderJob={renderJob}
                  result={result}
                  canRender={Boolean(params) && ready0 && ready1 && isFinal(renderJob) && Boolean(health?.ffmpeg.ok)}
                  onRender={startRender}
                  onCancel={cancelRender}
                  onGoto={setStep}
                />
              )}
            </>
          )}
        </main>

        <footer className="bottombar">
          <button type="button" className="btn ghost" disabled={shownStep === 0} onClick={() => setStep(shownStep - 1)}>
            <Icon name="left" /> Back
          </button>
          <div className="chips" aria-label="Your choices">
            {project.video && (
              <span className="chip static">
                <Icon name="film" size={14} /> {project.video.name}
              </span>
            )}
            {project.song && (
              <span className="chip static">
                <Icon name="music" size={14} /> {segLen ? `${segLen.toFixed(0)} s` : project.song.name}
              </span>
            )}
            {presets && (ready0 || ready1) && (
              <span className="chip static">
                <Icon name="palette" size={14} /> {presetTitles[project.preset] ?? project.preset}
              </span>
            )}
          </div>
          {shownStep < steps.length - 1 ? (
            <div className="next-wrap">
              {!canNext && nextHint && <span className="muted small next-hint">{nextHint}</span>}
              <button type="button" className="btn primary" disabled={!canNext} onClick={() => setStep(shownStep + 1)}>
                {shownStep === 3 ? "Next: render" : "Next"} <Icon name="right" />
              </button>
            </div>
          ) : (
            <span />
          )}
        </footer>

        <ReelsDrawer
          open={drawer}
          reels={reels}
          presetTitles={presetTitles}
          onClose={() => setDrawer(false)}
          onPlay={(j) => {
            setDrawer(false);
            setViewing(j);
          }}
          onOpenSettings={loadSettings}
          onChanged={refreshReels}
        />
        {viewing && <ReelViewer job={viewing} onClose={() => setViewing(null)} onUseSettings={loadSettings} />}
        <Toasts toasts={toasts} onDismiss={dismiss} />
      </div>
    </NotifyContext.Provider>
  );
}
