// One-click light / dark switch for the top bar.

import { setTheme, useTheme } from "../lib/theme";

function Sun() {
  return (
    <svg aria-hidden viewBox="0 0 20 20" className="size-5">
      <circle cx="10" cy="10" r="3.6" fill="currentColor" />
      <g stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
        {[0, 45, 90, 135, 180, 225, 270, 315].map((a) => (
          <line key={a} x1="10" y1="2" x2="10" y2="3.8" transform={`rotate(${a} 10 10)`} />
        ))}
      </g>
    </svg>
  );
}

function Moon() {
  return (
    <svg aria-hidden viewBox="0 0 20 20" className="size-5">
      <path d="M15.8 12.6A6.6 6.6 0 0 1 7.4 4.2a6.6 6.6 0 1 0 8.4 8.4Z" fill="currentColor" />
    </svg>
  );
}

export function ThemeToggle() {
  const theme = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
      className="pressable flex size-11 items-center justify-center rounded-md border border-line text-muted hover:border-muted hover:text-text"
    >
      {/* shows the mode you'd switch TO */}
      {theme === "dark" ? <Sun /> : <Moon />}
    </button>
  );
}
