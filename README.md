# PaintReel

Turns long painting videos (a watercolor session from a few minutes to 2 h+) into 9:16 Instagram Reels cut to a song.
PaintReel watches the painting for visible **moments**: a new wash, a new colour, detail work, colour blooming on wet
paper. It lands those moments on the **beats** of the music. The painting always moves forward in time. The edit only
chooses which moments to show, how fast to move between them, and where to cut.

Each render produces `reel.mp4` (1080×1920, 30 fps, H.264, AAC 48 kHz, about −14 LUFS), `reel_silent.mp4` (the identical
video without audio, for adding the song from Instagram's music library), a `cover.jpg`, the edit plan (`edl.json`) and
diagnostic charts.

## Setup and running

Requirements: Python 3.11+ (3.13 tested), Node 18+, and `ffmpeg`/`ffprobe` on PATH (`brew install ffmpeg`). Runs on CPU only.

```bash
make install        # Python venv (backend/.venv) + npm install
make dev            # API on :8000 + web app on http://127.0.0.1:5173 (Ctrl-C stops both)
make test           # backend tests (synthetic video + click track)
```

Open **http://127.0.0.1:5173** and follow the five steps:

1. **Painting**: drag and drop your video, or click to browse. It's analyzed once (about 40 s for a 9-minute phone
   video) and cached. You can pick the music while it runs. You'll see the start and finished frames, a timeline of the
   detected moments, and the stretches that will be skipped (idle, painter away, camera bumps, lighting changes).
2. **Music**: drop a song. Drag the handles on the waveform to choose the part to use (the most energetic 30 s is
   suggested). The 15/30/45/60 s buttons resize it, and ▶ plays just that part. The orange zone is the final hold on the
   finished painting.
3. **Style**: choose Energetic, Calm, Reveal or Smooth timelapse, then adjust *Pace*, *Cuts*, *Smooth transitions* and
   *Final hold*. The storyboard updates live with one tile per segment, so you can judge the edit before rendering.
4. **Framing**: the paper outline is detected automatically. Drag its corners to adjust it, then choose **Fill the
   screen** (crop to 9:16) or **Show the whole painting** (blurred background). The phone preview shows the result.
5. **Render**: watch progress (you can cancel), then play the reel in the phone frame. Download it with or without
   music, or use Share / Save to Photos on phones.

**My reels** (top right) lists every render. Play it, download it, load its settings back into the steps, or delete it.
Your choices are remembered in the browser, so a refresh doesn't lose your work. The layout works with touch and adapts
to tablets and phones.

### CLI

```bash
cd backend && export PYTHONPATH=src
.venv/bin/python -m paintreel analyze --video painting.mp4
.venv/bin/python -m paintreel render --video painting.mp4 --song song.mp3 --start 0:42 --end 1:12 \
    --preset energetic --out out/ [--control pace=0.8 --control cuts=0.3] [--debug] \
    [--crop x,y,w,h --crop-mode pad] [--set planner.events.coverage=0.7]
.venv/bin/python -m paintreel render-edl --edl out/edl.json --out out2/   # re-render an edited plan
.venv/bin/python -m paintreel presets [calm]
```

Leave out `--start`/`--end` to use the suggested part of the song. `--debug` also renders `reel_debug.mp4`, which burns in
the segment, style, live source time, progress and beat flashes. `PYTHONPATH=src` is needed because macOS can hide
editable-install `.pth` files (iCloud Desktop), and Python then skips them.

### Configuration

Every tuning number lives in `backend/config/paintreel.yaml`: the defaults, the presets, the style-card texts, and how
the four UI sliders map onto real settings. Put per-machine changes in `backend/config/config.local.yaml` (gitignored).
For example, `app.media_dirs` lists folders whose videos/songs appear next to your uploads, and `app.max_upload_mb`
sets the upload limit.

## How it works

### 1. Video analysis (`video_analysis.py`, once per video, cached)

ffmpeg pipes frames at 1 per second (keyframes only on long files) into small thumbnails stored on disk. The video is
never loaded into memory. For each sample:

- **Hand-free "clean canvas"**: the median of the surrounding ~15 s. Everything else is measured on it, so the hand
  and brush don't count as painting.
- **Visual signals**:
  - **detail** (Laplacian variance) and **edge density**, which rise during detail work
  - **colourfulness** and **colour-histogram change**, which flag new colours
  - **changed area**, which flags washes
- **Occlusion**: how much of the paper the hand covers (difference from the clean canvas, with help from a skin-tone
  check).
- **Progress**: a blend of likeness to the finished painting and cumulative paint change. The second part matters
  because layered watercolour often looks *less* like the final piece during the underpainting.
- **Moments (events)**: peaks of the combined visual-change score. Each is classed as *wash*, *new colour*, *detail*,
  *bloom* (change with no hand on the paper) or *change*, with a strength and hand-free *before* and *after* frames.
- **Skipped stretches**: camera bumps (phase correlation, confirmed by 3 of 4 frame quadrants), lighting jumps, idle
  time and painter absent.
- **The paper** (canvas bounding box) and **the finished frame**: the middle of the first hand-free stretch after the
  painting stops changing.

### 2. Song analysis (`audio.py`)

Beats are found with librosa and each one is snapped to its exact onset. Downbeats are every 4th beat, with the phase
chosen by onset strength. Energy is smoothed RMS. Sections come from librosa segmentation. The **drop** is the biggest
bar-scale rise in energy. The auto-suggested part is the most energetic window that starts on a downbeat.

### 3. Planning the edit (`planner.py`, `plan_events.py`, `plan_smooth.py`)

**Events strategy** (Energetic, Calm, Reveal): the moments and the beats are both in time order, so the edit is an
order-preserving **alignment**, solved by dynamic programming. The score:

- **rewards** showing strong moments, especially on strong beats (downbeats, section starts, and above all the drop)
- **penalizes** gaps between cuts that differ from the energy-dependent target (busy music means more cuts)
- **penalizes** a moment landing far from its share of the painting's work along the reel (*pacing*), so the whole
  painting is covered, underpainting included

Each chosen moment becomes a segment whose style follows the music:

- **jump** (loud): plays the moment quickly, then holds its hand-free result until the next beat, for a stop-motion
  build-up
- **flow**: an eased timelapse up to the next moment
- **moment** (quiet): the brushwork itself at near real speed

Calm boundaries get crossfades, where colour seems to bloom. The reel ends on a hold of the finished painting with a slow
zoom-out. For **Reveal**, moments past `pre_drop_progress` can't appear before the drop.

**Smooth strategy** (Smooth timelapse): one continuous timelapse. A content clock (activity-weighted, zero on skipped
stretches) is spread over beat-aligned chunks at speeds driven by energy. Extra cuts are added when needed to jump over
skipped stretches.

Both strategies only ever move forward through the source. `validate_edl` enforces this, including inside speed curves.

### 4. Rendering (`render.py`)

Every segment is frame-accurate:

1. The source time of each output frame comes from the segment's speed curve (ramps, play-then-hold).
2. ffmpeg decodes candidate frames, cropped and scaled once.
3. In fast timelapses, the **least-occluded candidate inside each frame's time slot** is used, so far fewer hands
   flicker through.

Segments are joined with concat (cuts) and xfade (crossfades) on a fixed frame grid. The audio is trimmed, faded,
loudness-normalized in two passes and muxed. The silent version is a stream copy of the same video.

## Tuning on your footage

Most of the time the style cards and the four sliders are enough. The underlying settings, most useful first:

| setting | what it changes |
|---|---|
| `planner.events.jump_energy_min`, `moment_energy_max` | which music energy gets stop-motion jumps vs. real-time moments |
| `planner.cuts.min/max_beats_per_cut` | cut density at high / low energy |
| `planner.events.pace_weight`, `coverage` | even coverage of the whole painting vs. only its strongest moments |
| `analysis.events.min_score`, `min_gap_seconds` | how many moments are detected |
| `analysis.events.weights` | which kinds of change count most (area / colour / detail / edges) |
| `analysis.occlusion_diff_threshold`, `skin_cr/skin_cb`, `clean_threshold` | hand detection (see the occlusion curve in the analysis chart) |
| `analysis.idle_min_seconds`, `idle_progress_eps` | what counts as "nothing happening" |
| `render.clean_sampling`, `candidates_per_frame` | hand-free frame picking in fast timelapses |
| `render.crop_mode`, `max_crop_loss` | fill vs. whole-painting framing |

Diagnostic charts are in each render's folder (`backend/data/outputs/<id>/`): `analysis.png` (progress, moments,
detail/colour/activity, occlusion) and `mapping.png` (output time → painting time, by segment style, with beats and
energy).

## Layout

```
backend/config/paintreel.yaml   all settings, presets, style cards, slider mappings
backend/src/paintreel/          audio, video_analysis, planner (+ plan_events, plan_smooth), render, plots, pipeline, cli
backend/src/paintreel/api/      FastAPI app, background jobs (cancel, delete), uploads
backend/tests/                  synthetic painting + click track, pipeline and API tests
frontend/src/                   React + TypeScript app: steps/, components/, api.ts, state.ts
```

Data lives in `backend/data/`: `uploads/` (your files), `cache/` (analysis, reused across renders) and `outputs/` (one
folder per render, which is also the history).

## Known limitations

- Moments are found at 1 sample per second, so very quick changes are merged.
- A hand that rests still on the paper for many seconds can become part of the "clean canvas".
- Very pale layers register as small changes. Raise `planner.events.coverage` or `pace_weight` if early phases are
  skipped.
- Downbeats assume 4/4. Sections and the drop are heuristics.
- A camera bump that permanently moves the framing is skipped, but the crop isn't re-aligned afterwards.
