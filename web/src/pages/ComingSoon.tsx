import { Link } from "react-router";
import { SectionHeader } from "../components/ui";

export function ComingSoon({ label, title, text }: { label: string; title: string; text: string }) {
  return (
    <section aria-labelledby="soon-title">
      <SectionHeader label={label} title={title} id="soon-title" />
      <div className="rounded-lg border border-dashed border-line px-6 py-14 text-center">
        <p className="text-lg font-bold">Coming soon</p>
        <p className="mx-auto mt-1 max-w-md text-muted">{text}</p>
        <Link
          to="/race"
          className="pressable mt-5 inline-flex min-h-11 items-center rounded-md border border-line bg-surface px-4 font-bold hover:border-muted"
        >
          Go to the latest race
        </Link>
      </div>
    </section>
  );
}

export function NotFound() {
  return (
    <ComingSoon label="404" title="Page not found" text="That page doesn't exist. The race pages are a good place to start." />
  );
}
