import { useEffect, useState } from "react";
import { AppTopBar } from "./AppTopBar.tsx";
import { BookCard, type Hero } from "./BookCard.tsx";
import { OutOfTokensNote, StartFailedPanel, type StartState } from "./StartStates.tsx";
import { books as loadBooks, type Book } from "./graphql.ts";
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

export default function BookList({ onPick, onOwnIdea, start = idle }: { onPick: (book: Book) => void; onOwnIdea: () => void; start?: StartState }) {
  const [books, setBooks] = useState<Book[]>([]);

  useEffect(() => {
    loadBooks()
      .then(setBooks)
      .catch(() => setBooks([]));
  }, []);

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
        <div className="grid w-full max-w-1080 grid-cols-[repeat(auto-fit,minmax(min(300px,100%),1fr))] gap-20">
          {books.map((b, i) => (
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
          <BookCard title="Your own book idea" author="Describe it and start from scratch" hero={ownHero} lazy disabled={!!start.outOfTokens} onClick={onOwnIdea} />
        </div>
      </main>
    </div>
  );
}
