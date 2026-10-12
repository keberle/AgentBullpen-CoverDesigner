import { KitButton } from "./design-kit/components/KitButton.tsx";
import type { BookInput } from "./graphql.ts";

// What the pick screen and the own-idea form show while a design session starts (App.tsx holds the state; the texts are in
// problems.ts). The opening is the first model call, so a provider that can't serve shows up here first.

/** A book that didn't open, and why (problems.ts's startView). */
export type StartFailure = { book: BookInput; reason: string };

export type StartState = {
  /** The book whose session is starting; null when none is. */
  opening: BookInput | null;
  failure: StartFailure | null;
  /** The notice when today's allowance is used up, or the designer is off for the account; null otherwise (and always standalone). */
  outOfTokens: string | null;
  /** Repeats the failed start. */
  onRetry: () => void;
};

/** "The designer couldn't open Treasure Island — …", with Try again. */
export function StartFailedPanel({ failure, onRetry }: { failure: StartFailure; onRetry: () => void }) {
  return (
    <div role="alert" className="box-border flex w-full max-w-720 items-center gap-16 rounded-12 border border-outline bg-panel px-16 py-14">
      <span className="grow text-15 leading-[1.5]">
        The designer couldn't open <b>{failure.book.title}</b> — {failure.reason}
      </span>
      <KitButton variant="outline" size="action" onClick={onRetry} className="shrink-0">
        Try again
      </KitButton>
    </div>
  );
}

/** "You've used today's $1.00. The designer comes back at 6:00 PM." */
export function OutOfTokensNote({ text }: { text: string }) {
  return (
    <div role="status" className="text-center text-14 text-fg-muted">
      {text}
    </div>
  );
}
