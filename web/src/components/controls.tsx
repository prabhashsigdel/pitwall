// Form controls: real <select>s and switch buttons with visible labels, 44px tall.

import { useId } from "react";

export interface Option {
  value: string;
  label: string;
}

export function Select({ label, value, options, onChange, className = "" }: {
  label: string;
  value: string;
  options: Option[];
  onChange: (value: string) => void;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={`flex flex-col gap-1 ${className}`}>
      <label htmlFor={id} className="text-xs font-bold uppercase tracking-[0.1em] text-muted">
        {label}
      </label>
      <div className="relative">
        <select
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="min-h-11 w-full cursor-pointer appearance-none rounded-md border border-line bg-inner py-2 pl-3 pr-9 font-semibold text-text transition-colors duration-150 hover:border-muted"
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        {/* chevron */}
        <svg aria-hidden viewBox="0 0 12 12" className="pointer-events-none absolute right-3 top-1/2 size-3 -translate-y-1/2 text-muted">
          <path d="M2 4.5 6 8l4-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
      </div>
    </div>
  );
}

/** On/off switch. The whole row (label + track) is the 44px tap target. */
export function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="group flex min-h-11 items-center gap-2.5 rounded-md pr-1 text-sm font-semibold"
    >
      <span
        aria-hidden
        className={`relative h-6 w-10 shrink-0 rounded-full border transition-colors duration-150 ${
          checked ? "border-accent bg-accent" : "border-line bg-inner group-hover:border-muted"
        }`}
      >
        <span
          className={`absolute left-0.5 top-0.5 size-4 rounded-full transition-transform duration-200 ease-out motion-reduce:transition-none ${
            checked ? "translate-x-4 bg-white" : "translate-x-0 bg-muted"
          }`}
        />
      </span>
      {label}
    </button>
  );
}
