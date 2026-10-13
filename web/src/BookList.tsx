import { useEffect, useState } from "react";
import { AppTopBar } from "./AppTopBar.tsx";
import { BookCard, type Hero } from "./BookCard.tsx";
import { OutOfTokensNote, StartFailedPanel, type StartState } from "./StartStates.tsx";
import { KitButton } from "./design-kit/components/KitButton.tsx";
import { AppUnavailableError, books as loadBooks, gatewayMode, SignedOutError, siteUrl, type Book } from "./graphql.ts";
import hound768 from "./assets/book-hound-768.webp";
import hound1536 from "./assets/book-hound-1536.webp";
import musketeers768 from "./assets/book-musketeers-768.webp";
import musketeers1536 from "./assets/book-musketeers-1536.webp";
import treasure768 from "./assets/book-treasure-island-768.webp";
import treasure1536 from "./assets/book-treasure-island-1536.webp";
import own768 from "./assets/book-own-768.webp";
import own1536 from "./assets/book-own-1536.webp";

// "Pick a book" screen (the pick mockup). Books come from the GraphQL api,
// followed by the fixed "Your own book idea" card, each with its hero image.
// Clicking a book opens the design screen; "Your own book idea" opens its form.
// Deployed, the api may be asleep and take up to a minute to answer the first call. Until the books are here: a status
// line and four skeleton cards, with a second line after ten seconds; the books and the own-idea card then appear
// together. A call that failed or timed out: an alert with Try again, and no cards.
// While a book's session starts its card reads "Opening…"; a book that didn't open gets a panel above the cards with
// Try again; with today's allowance used up, or the designer off for the account, a note above the cards and every card
// off (`start`, from App.tsx).

const idle: StartState = { opening: null, failure: null, outOfTokens: null, onRetry: () => {} };

// Hero images and alt texts from the mockup, by book title.
const heroes: Record<string, Hero> = {
  "The Hound of the Baskervilles": {
    src768: hound768,
    src1536: hound1536,
    alt: "Holmes and Watson crouch behind rocks on the moor at night, a glowing hound on the ridge",
  },
  "The Three Musketeers": {
    src768: musketeers768,
    src1536: musketeers1536,
    alt: "Four musketeers cross their rapiers in front of a stone palace",
  },
  "Treasure Island": {
    src768: treasure768,
    src1536: treasure1536,
    alt: "A pirate captain with a parrot, a young sailor with a spyglass and a boy at a ship's rail, an island and a pirate ship behind them",
  },
};
const ownHero: Hero = { src768: own768, src1536: own1536, alt: "A writer at her desk, pen to chin, a notebook headed My Novel in front of her" };

/** After this long still loading, the status says why. */
const slowAfterMs = 10_000;

const skeletonBar = "rounded-6 bg-[#2A2A2A] motion-safe:animate-wait-pulse";

/** A card's place while the list loads. */
function SkeletonCard() {
  return (
    <div aria-hidden="true" className="flex flex-col overflow-hidden rounded-16 border border-divider bg-panel">
      <div className="aspect-[3/2] w-full bg-[#2A2A2A] motion-safe:animate-wait-pulse" />
      <div className="flex flex-col gap-10 px-24 pt-20 pb-24">
        <div className={`h-18 w-3/4 ${skeletonBar}`} />
        <div className={`h-12 w-1/2 ${skeletonBar}`} />
      </div>
    </div>
  );
}

export default function BookList({ onPick, onOwnIdea, start = idle }: { onPick: (book: Book) => void; onOwnIdea: () => void; start?: StartState }) {
  // null = not here yet. `failed`: the call failed or timed out (graphql.ts's booksTimeoutMs). `attempt` counts Try again.
  const [books, setBooks] = useState<Book[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [slow, setSlow] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    const timer = setTimeout(() => setSlow(true), slowAfterMs);
    loadBooks().then(
      (list) => {
        if (current) setBooks(list);
      },
      (e: unknown) => {
        if (!current) return;
        // The visitor is on the way to the site's under-construction page or its sign-in page (graphql.ts sent them).
        if (e instanceof AppUnavailableError || (e instanceof SignedOutError && gatewayMode && siteUrl)) return;
        setFailed(true);
      },
    );
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [attempt]);

  const retry = () => {
    setFailed(false);
    setSlow(false);
    setAttempt((n) => n + 1);
  };
  const waiting = !books && !failed;

  return (
    <div className="flex h-screen flex-col">
      <AppTopBar />
      <main className="flex grow flex-col items-center gap-32 overflow-y-auto px-24 py-56">
        <div className="flex flex-col items-center gap-8 text-center">
          <h1 className="m-0 text-40 font-extrabold tracking-[-1px]">Pick a book</h1>
          <p className="m-0 text-17 text-fg-muted">Then talk to the cover designer to shape its cover.</p>
        </div>
        {start.failure && <StartFailedPanel failure={start.failure} onRetry={start.onRetry} />}
        {start.outOfTokens && <OutOfTokensNote text={start.outOfTokens} />}
        {failed && (
          <div role="alert" className="box-border flex w-full max-w-720 items-center gap-16 rounded-12 border border-outline bg-panel px-16 py-14">
            <span className="grow text-15 leading-[1.5]">The cover designer didn't start. Try again in a moment.</span>
            <KitButton variant="outline" size="action" onClick={retry} className="shrink-0">
              Try again
            </KitButton>
          </div>
        )}
        {waiting && (
          <div role="status" aria-live="polite" className="flex flex-col items-center gap-6 text-center">
            <span className="flex items-center gap-10 text-16 font-semibold">
              <span aria-hidden="true" className="box-border size-14 rounded-999 border-2 border-[rgba(255,255,255,0.25)] border-t-accent motion-safe:animate-wait-spin" />
              Starting the cover designer…
            </span>
            {slow && (
              <span className="max-w-520 text-14 leading-[1.5] text-fg-muted">
                It sleeps when nobody is using it. The first visit after a quiet spell takes up to a minute.
              </span>
            )}
          </div>
        )}
        {!failed && (
          <div className="grid w-full max-w-1080 grid-cols-[repeat(auto-fit,minmax(min(300px,100%),1fr))] gap-20">
            {!books && [0, 1, 2, 3].map((i) => <SkeletonCard key={i} />)}
            {books?.map((b, i) => (
              <BookCard
                key={b.title}
                title={b.title}
                author={b.author}
                hero={heroes[b.title]}
                lazy={i > 0}
                opening={start.opening?.title === b.title}
                disabled={!!start.outOfTokens}
                onClick={() => onPick(b)}
              />
            ))}
            {books && (
              <BookCard title="Your own book idea" author="Describe it and start from scratch" hero={ownHero} lazy disabled={!!start.outOfTokens} onClick={onOwnIdea} />
            )}
          </div>
        )}
      </main>
    </div>
  );
}
