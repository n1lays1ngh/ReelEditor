import { api } from "../api";
import { AssetGrid } from "../components/AssetGrid";
import { DropZone } from "../components/DropZone";
import { Waveform } from "../components/Waveform";
import type { Asset, SongOverview } from "../types";

interface Props {
  song: { path: string; name: string } | null;
  assets: Asset[];
  assetsLoading: boolean;
  maxMb: number;
  overview: SongOverview | null;
  overviewLoading: boolean;
  segment: [number, number] | null;
  holdSeconds: number;
  onSelect: (a: { path: string; name: string }) => void;
  onDeleted: (path: string) => void;
  onSegment: (start: number, end: number) => void;
}

const EXT = [".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"];

export function StepMusic(p: Props) {
  const len = p.segment ? p.segment[1] - p.segment[0] : 0;
  return (
    <div className="step-body">
      <header className="step-header">
        <h2>Music</h2>
        <p className="muted">Add a song, then choose the part your Reel will use. Cuts and speed will follow its beat.</p>
      </header>
      <DropZone
        kind="song"
        title="Drop a song here, or click to browse"
        hint="MP3, WAV, M4A, AAC, FLAC, OGG"
        accept="audio/*,.m4a,.flac"
        extensions={EXT}
        maxMb={p.maxMb}
        compact
        onUploaded={p.onSelect}
      />
      {p.assets.length > 0 && <h3 className="section-title">Your songs</h3>}
      <AssetGrid
        kind="song"
        assets={p.assets}
        selected={p.song?.path ?? null}
        loading={p.assetsLoading}
        onSelect={(x) => p.onSelect({ path: x.path, name: x.name })}
        onDeleted={p.onDeleted}
      />
      {p.song && (
        <section className="card">
          <div className="card-head">
            <h3>{p.song.name}</h3>
            {p.segment && p.overview && Math.abs(p.segment[0] - p.overview.suggested_start) < 0.05 && Math.abs(p.segment[1] - p.overview.suggested_end) < 0.05 && (
              <span className="badge">Suggested part</span>
            )}
          </div>
          {p.overviewLoading && !p.overview && <div className="skeleton wave-skeleton">Finding the beat…</div>}
          {p.overview && p.segment && (
            <>
              <Waveform
                overview={p.overview}
                audioUrl={api.mediaUrl(p.song.path)}
                start={p.segment[0]}
                end={p.segment[1]}
                holdSeconds={p.holdSeconds}
                onChange={p.onSegment}
              />
              <p className="muted small">
                Your Reel will be <strong>{len.toFixed(1)} s</strong> long. The last ~{p.holdSeconds.toFixed(1)} s (orange) shows the finished
                painting. Drag the handles to change the part; tap elsewhere to move it.
              </p>
            </>
          )}
        </section>
      )}
    </div>
  );
}
