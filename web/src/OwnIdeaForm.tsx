import { useState } from "react";
import { AppTopBar } from "./AppTopBar.tsx";
import { KitButton } from "./design-kit/components/KitButton.tsx";
import { OutOfTokensNote, StartFailedPanel, type StartState } from "./StartStates.tsx";
import type { BookInput } from "./graphql.ts";

// "Your own book idea" (the own-idea mockup): the form between the pick
// screen and the design screen. Required fields use the browser's own
// validation (the mockup marks them `required`). While the session starts the submit button reads "Opening…"; a start
// that failed gets the same panel as the pick screen, above the button; with the allowance used up or the designer off,
// the note and the button off.

const idle: StartState = { opening: null, failure: null, outOfTokens: null, onRetry: () => {} };

// The cover style presets, in the mockup's order and words. The api maps each id to its style block (api/styles/).
const styles = [
  ["designer", "Let the designer propose one"],
  ["classic", "Classic paperback — restrained palette, one strong image, title set large"],
  ["minimal", "Minimal typographic — type does the work, flat color, little or no imagery"],
  ["painted", "Painted illustration — brushwork, warm light, storybook feel"],
  ["cinematic", "Cinematic — photoreal, dramatic lighting, movie-poster framing"],
  ["animated", "Animated — 3D-animated-film look, expressive characters, bright"],
  ["other", "Other…"],
] as const;

const label = "flex flex-col gap-6 text-14 text-fg-muted";
const field = "box-border rounded-8 border border-outline bg-panel font-sans text-16 text-fg";
const input = `${field} min-h-48 px-14`;
const textarea = `${field} resize-y px-14 py-12`;

export default function OwnIdeaForm({ onStart, onBack, start = idle }: { onStart: (book: BookInput) => void; onBack: () => void; start?: StartState }) {
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [genre, setGenre] = useState("");
  const [about, setAbout] = useState("");
  const [style, setStyle] = useState("designer");
  const [styleText, setStyleText] = useState("");

  return (
    <div className="flex h-screen flex-col">
      <AppTopBar />
      <main className="flex grow justify-center overflow-y-auto px-24 py-56">
        <form
          className="flex w-full max-w-560 flex-col gap-20"
          onSubmit={(e) => {
            e.preventDefault();
            if (start.opening || start.outOfTokens) return;
            onStart({ title: title.trim(), author: author.trim(), genre: genre.trim(), about: about.trim(), style, styleText: style === "other" ? styleText.trim() : "" });
          }}
        >
          <div className="flex flex-col gap-8">
            <h1 className="m-0 text-36 font-extrabold tracking-[-0.5px]">Your own book idea</h1>
            <p className="m-0 text-16 text-fg-muted">The designer works from what you write here. Title and author go on the cover as you type them.</p>
          </div>
          <label className={label}>
            Working title
            <input type="text" required value={title} onChange={(e) => setTitle(e.target.value)} className={input} />
          </label>
          <label className={label}>
            Author name
            <input type="text" required value={author} onChange={(e) => setAuthor(e.target.value)} className={input} />
          </label>
          <label className={label}>
            Genre
            <input type="text" placeholder="e.g. cozy mystery, space opera, literary fiction" value={genre} onChange={(e) => setGenre(e.target.value)} className={input} />
          </label>
          <label className={label}>
            What's it about?
            <textarea
              required
              placeholder="Two or three sentences: who, where, what's at stake."
              value={about}
              onChange={(e) => setAbout(e.target.value)}
              className={`${textarea} min-h-120`}
            />
          </label>
          <label className={label}>
            Cover style
            <select value={style} onChange={(e) => setStyle(e.target.value)} className={input}>
              {styles.map(([id, text]) => (
                <option key={id} value={id}>
                  {text}
                </option>
              ))}
            </select>
          </label>
          {style === "other" && (
            <label className={label}>
              Describe the style
              <textarea
                required
                placeholder="Mood, palette, imagery, a cover you admire — anything that helps."
                value={styleText}
                onChange={(e) => setStyleText(e.target.value)}
                className={`${textarea} min-h-96`}
              />
            </label>
          )}
          {start.failure && <StartFailedPanel failure={start.failure} onRetry={start.onRetry} />}
          {start.outOfTokens && <OutOfTokensNote text={start.outOfTokens} />}
          <KitButton size="md" type="submit" disabled={!!start.opening || !!start.outOfTokens}>
            {start.opening ? "Opening…" : "Start designing"}
          </KitButton>
          <KitButton variant="text" size="sm" onClick={onBack}>
            Back
          </KitButton>
        </form>
      </main>
    </div>
  );
}
