import type { AllowanceView } from "./allowance.ts";

// Today's allowance under the designer's input, behind the site only (the allowance mockup's footer): what is left of the
// day's dollars, when it resets, and a thin meter that turns to the warning colour when little is left. Off: one line,
// no reset time and an empty meter.

export function AllowanceFooter({ view }: { view: AllowanceView }) {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex justify-between gap-8 text-13">
        <span>{view.left}</span>
        {view.reset && <span className="text-fg-muted">{view.reset}</span>}
      </div>
      <div role="meter" aria-label="Today's allowance left" aria-valuemin={0} aria-valuemax={100} aria-valuenow={view.percent} className="h-6 overflow-hidden rounded-3 bg-divider">
        <div className={`h-full rounded-3 ${view.state === "low" ? "bg-warning" : "bg-accent"}`} style={{ width: `${view.percent}%` }} />
      </div>
    </div>
  );
}

/** The notice above the input when the designer is used up for today, or off for the account. */
export function AllowanceNotice({ text }: { text: string }) {
  return (
    <div role="status" className="rounded-10 bg-panel p-12 text-center text-14 leading-[1.45]">
      {text}
    </div>
  );
}
