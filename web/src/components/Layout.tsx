// App shell: top bar (wordmark, nav tabs, season info) and the centred content column.

import { NavLink, Outlet } from "react-router";
import { useMeta } from "../api/hooks";
import { ThemeToggle } from "./ThemeToggle";

const TABS = [
  { to: "/race", label: "Race" },
  { to: "/season", label: "Season" },
  { to: "/sim", label: "Strategy sim" },
  { to: "/circuits", label: "Circuits" },
];

function SeasonInfo() {
  const meta = useMeta();
  if (meta.isPending) return <span className="h-4 w-44 animate-pulse-soft rounded bg-line/70" aria-hidden />;
  if (meta.isError) return <span className="text-sm text-bad">API offline</span>;
  return (
    <span className="text-sm text-muted">
      Season {meta.data.season} · {meta.data.races_loaded} races loaded
    </span>
  );
}

export function Layout() {
  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-20 border-b border-line bg-bg/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1280px] flex-wrap items-center gap-x-8 px-4 sm:px-6">
          <NavLink to="/race" className="py-3 text-2xl font-black tracking-[0.04em]" aria-label="PITWALL home">
            PIT<span className="text-accent-text">WALL</span>
          </NavLink>

          <nav aria-label="Main" className="order-3 -mx-4 w-[calc(100%+2rem)] overflow-x-auto px-4 sm:order-none sm:mx-0 sm:w-auto sm:px-0">
            <ul className="flex gap-1">
              {TABS.map((t) => (
                <li key={t.to}>
                  <NavLink
                    to={t.to}
                    className={({ isActive }) =>
                      `relative flex min-h-11 items-center whitespace-nowrap px-3 font-semibold transition-colors duration-150 ${
                        isActive ? "text-text" : "text-muted hover:text-text"
                      }`
                    }
                  >
                    {({ isActive }) => (
                      <>
                        {t.label}
                        {/* active tab: red underline */}
                        <span
                          aria-hidden
                          className={`absolute inset-x-3 -bottom-px h-0.5 rounded-full bg-accent transition-opacity duration-150 ${
                            isActive ? "opacity-100" : "opacity-0"
                          }`}
                        />
                      </>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>

          <div className="ml-auto flex items-center gap-4 py-1.5">
            <SeasonInfo />
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1280px] px-4 pb-20 pt-8 sm:px-6">
        <Outlet />
      </main>
    </div>
  );
}
