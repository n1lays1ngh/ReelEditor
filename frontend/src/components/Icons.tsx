// Small inline SVG icon set (stroke icons, 24px grid).
import type { SVGProps } from "react";

const PATHS: Record<string, string> = {
  upload: "M12 16V4m0 0-4 4m4-4 4 4M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3",
  film: "M4 5h16v14H4zM8 5v14M16 5v14M4 9h4M4 15h4M16 9h4M16 15h4",
  music: "M9 18V6l11-2v12M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm11-2a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  palette: "M12 3a9 9 0 1 0 0 18c1 0 1.5-.8 1.5-1.5 0-1.2-1-1.5-1-2.5S13 15 14 15h2a5 5 0 0 0 5-5c0-3.9-4-7-9-7ZM7.5 11.5h.01M9.5 7.5h.01M14.5 7.5h.01",
  crop: "M6 2v14a2 2 0 0 0 2 2h14M2 6h14a2 2 0 0 1 2 2v14",
  play: "M7 4.5v15l12-7.5z",
  pause: "M7 5h3v14H7zM14 5h3v14h-3z",
  download: "M12 4v12m0 0-4-4m4 4 4-4M4 20h16",
  share: "M12 3v12M12 3 8 7m4-4 4 4M5 12v7a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-7",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3",
  check: "M5 12.5 10 17l9-10",
  x: "M6 6l12 12M18 6 6 18",
  bolt: "M13 2 4 14h7l-1 8 9-12h-7z",
  leaf: "M5 19c0-8 5-14 15-15-1 10-7 15-15 15Zm0 0 7-7",
  sparkle: "M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z",
  wave: "M2 12c2.5-5 5-5 7.5 0s5 5 7.5 0 3.5-3.5 5-2",
  brush: "M14 4l6 6-8 8H6v-6zM4 20c0-2 1-3 3-3",
  left: "M15 5l-7 7 7 7",
  right: "M9 5l7 7-7 7",
  reels: "M4 4h16v16H4zM4 9h16M9 4l2 5M14 4l2 5M10 13v5l4-2.5z",
  info: "M12 8h.01M11 12h1v5h1M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  settings: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4",
  stop: "M6 6h12v12H6z",
  clock: "M12 7v5l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z",
  expand: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  phone: "M8 2h8a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2ZM11 18h2",
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 18, ...rest }: { name: IconName | string; size?: number } & SVGProps<SVGSVGElement>) {
  const d = PATHS[name] ?? PATHS.brush;
  const filled = name === "play";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth={filled ? 0 : 1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      <path d={d} />
    </svg>
  );
}
