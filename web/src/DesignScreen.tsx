import { useEffect, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import { AllowanceFooter, AllowanceNotice } from "./AllowanceFooter.tsx";
import { AppTopBar } from "./AppTopBar.tsx";
import { allowanceView, costText, holdsOff, onAttention, refusedBudget, tokensAndCost } from "./allowance.ts";
import { KitButton } from "./design-kit/components/KitButton.tsx";
import {
  availability,
  budget as loadBudget,
  chat,
  downloadUrl,
  gatewayMode,
  GraphQLError,
  modelsAndBudget,
  session as loadSession,
  SignedOutError,
  siteUrl,
  startSession,
  type Book,
  type Budget,
  type ChatModel,
  type License,
  type Message,
  type Turn,
  type Legibility,
  type Typography,
  type Version,
} from "./graphql.ts";
import { modelLabel, showModelPicker } from "./gateway.ts";
import { signInUrl } from "./site.ts";
import {
  fallbackModel,
  fallbackNote,
  modelOptionLabel,
  modelSwitchNote,
  paintNote,
  problemOf,
  problemView,
  rateWaitText,
  signedOutText,
} from "./problems.ts";
import { CoverType, defaultTypography } from "./CoverType.tsx";

// Design screen — the chat, first-image, versions, license and typography mockups, at
// /design/:sessionId (?v=N selects version N; absent = the latest). Above the surface, the artistic license control:
// changing it sends nothing, the next chat carries it. The render surface shows the selected version's plate with the
// title and the author laid over it in HTML from the version's typography (CoverType.tsx); under it the versions strip
// and Download, which fetches the api's render of the same cover with the type baked in. The chat rail mirrors AgentBullpen-TemporalGraph's
// ChatPanel.tsx: an aria-live list that scrolls to the newest message, a "thinking…" line while waiting, error
// bubbles with the GraphQL error code, a "Details" line under each answer, one Try chip and an Enter-to-send form.
// Below md (phone width) the rail stacks under the surface and the strip wraps. The top bar names the book and has the
// Change book button, which goes back to the pick screen (AppTopBar.tsx); the browser tab reads "{title} · Book Cover".
// Gateway (the gateway mockup; TemporalGraph's ChatPanel): behind the AgentBullpen.AI gateway the top bar's right side
// is empty, a model picker sits under the book title when the site lists more than one chat model, the tokens left
// today show under the input, and the out-of-credits and signed-out states turn the input off. Standalone: unchanged.
// Failures (docs/design/errors.md; TemporalGraph's panel states): each failed turn shows its problem's text
// (problems.ts): a used-up budget turns the input off until its reset time and back on by itself; a rate limit says
// "One moment…" and sends once more; a model service down or a failed turn gets a Try again button; an answer whose paint
// failed gets a muted note under it, with Retry paint when trying again could help; an answer from the site's fallback
// model says so; an unavailable model is greyed out in the picker and the session moves to the default. Disabled
// controls look disabled (the kit's disabled tokens).

export const noModelNote = "No model configured — see README › Quickstart";
export const noImagesNote = "Image generation isn't configured — see docs › Configuration. The designer can still talk.";
const firstTry = "Paint your first idea for this cover.";
const fontTry = "Try a different font."; // after a turn that painted
const nextTry = "Try something bolder."; // after a typography change (or a turn that only talked)

// The four levels with the note shown under the control (verbatim from the design).
const licenses: { id: License; name: string; note: string }[] = [
  { id: "faithful", name: "Faithful", note: "Paints what you ask, literally. Keeps the book's style exactly." },
  { id: "interpretive", name: "Interpretive", note: "Weighs a few ideas and picks the best fit. Keeps the book's style, adapts it to your request." },
  { id: "bold", name: "Bold", note: "Skips the obvious idea for a symbolic one. The book's style is a starting point." },
  { id: "wild", name: "Wild", note: "A conceptual or abstract take; may drop the characters entirely. The book's style is optional." },
];
const licenseName = (id: License | null) => licenses.find((l) => l.id === id)?.name ?? id;

// error: `again` = the message a Try again button sends once more. note: a muted line (One moment…, a model switch).
type Msg =
  | { kind: "mine"; text: string }
  | { kind: "answer"; message: Message }
  | { kind: "error"; text: string; again?: string }
  | { kind: "note"; text: string };

const thousands = (n: number) => n.toLocaleString("en-US");

/** "The Hound of the Baskervilles" → "the-hound-of-the-baskervilles" (TemporalGraph's slug rule; accents dropped). */
export function slug(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function errorText(e: unknown): string {
  if (e instanceof GraphQLError && e.code) return `${e.message} (${e.code})`;
  return e instanceof Error ? e.message : String(e);
}

const fromSession = (messages: Message[]): Msg[] => messages.map((m) => (m.role === "user" ? { kind: "mine", text: m.text } : { kind: "answer", message: m }));

/** The small outline button under an error or a failed paint: Try again / Retry paint (36px, 13px). */
function RetryButton({ label, onClick, disabled }: { label: string; onClick: () => void; disabled: boolean }) {
  return (
    <KitButton variant="outline" size="xs" disabled={disabled} onClick={onClick} className="self-start">
      {label}
    </KitButton>
  );
}

/** Bubbles: max 85%, 12px 14px, radius 14, 15px / 1.5. Mine = accent + black text; answers on the panel color. An
 * answer's fallback and paint-failed notes are 13px muted lines under it; Retry paint only on the latest answer. */
function Bubble({
  msg,
  selected,
  versions,
  onShow,
  onAgain,
  onRetryPaint,
  busy,
}: {
  msg: Msg;
  selected: number | null;
  versions: Version[];
  onShow: (n: number) => void;
  onAgain: (message: string) => void;
  onRetryPaint?: () => void;
  busy: boolean;
}) {
  if (msg.kind === "mine") return <div className="max-w-[85%] self-end rounded-14 bg-accent px-14 py-12 text-15 leading-[1.5] text-on-accent">{msg.text}</div>;
  if (msg.kind === "note") return <div className="self-start px-4 text-13 text-fg-muted">{msg.text}</div>;
  if (msg.kind === "error") {
    const again = msg.again;
    return (
      <div className="flex max-w-[85%] flex-col gap-8 self-start">
        <div role="alert" className="rounded-14 border border-divider px-14 py-12 text-15 leading-[1.5] text-fg">
          {msg.text}
        </div>
        {again && <RetryButton label="Try again" disabled={busy} onClick={() => onAgain(again)} />}
      </div>
    );
  }
  const m = msg.message;
  const note = m.paintProblem ? paintNote(m.paintProblem, gatewayMode) : null;
  const usage = m.usage ?? { inputTokens: 0, outputTokens: 0 };
  const img = m.image;
  // With a painting: "Details · 1 image · N tokens · $0.0412", N = the chat tokens plus the image tokens the API reported;
  // the cost is the whole turn's (chat, concepts and the image), and is left out when the API has none.
  const tokens = usage.inputTokens + usage.outputTokens + (img?.inputTokens ?? 0) + (img?.outputTokens ?? 0);
  const n = m.version;
  // A link inside <summary> would also open/close the details: prevent that, then select the version.
  const show = (e: MouseEvent) => {
    e.preventDefault();
    if (n) onShow(n);
  };
  const versionAndShow = n && (
    <>
      Version {n} ·{" "}
      {n !== selected && (
        <>
          <a href={`?v=${n}`} onClick={show} className="text-accent">
            show
          </a>{" "}
          ·{" "}
        </>
      )}
    </>
  );
  // A typography turn (set_typography, no paint): "Version 2 · Typography · Details · N tokens · $0.0009" and the
  // settings on the version it made.
  if (m.typography && !img) return <TypographyAnswer m={m} t={m.typography} tokens={tokens} version={versionAndShow} />;
  const painted = n ? versions.find((v) => v.n === n) : undefined;
  return (
    <div className="flex max-w-[85%] flex-col gap-6 self-start">
      <div className="rounded-14 bg-panel px-14 py-12 text-15 leading-[1.5] whitespace-pre-wrap text-fg">{m.text}</div>
      {m.fallbackUsed && <div className="px-4 text-13 text-fg-muted">{fallbackNote(m.fallbackUsed)}</div>}
      {note && (
        <div role="status" className="px-4 text-13 text-fg-muted">
          {note.text}
        </div>
      )}
      {note?.retry && onRetryPaint && <RetryButton label="Retry paint" disabled={busy} onClick={onRetryPaint} />}
      <details className="px-4 text-13 text-fg-muted">
        <summary className="cursor-pointer">
          {versionAndShow}
          Details{img ? " · 1 image" : ""} · {tokensAndCost(tokens, usage)}
        </summary>
        <ul className="m-0 mt-6 flex list-none flex-col gap-4 p-0 font-mono text-12">
          {img ? (
            <>
              {painted && (
                <>
                  <li>
                    License: {licenseName(m.license ?? painted.license)} · Concept: {painted.concepts[painted.chosen]?.title ?? painted.concept}
                  </li>
                  {painted.concepts.length > 0 && (
                    <li>
                      Ideas considered ({painted.concepts.length}):
                      <ul className="m-0 mt-2 list-none p-0">
                        {/* "✓ " before the chosen one, two spaces before the others (pre keeps them). */}
                        {painted.concepts.map((c, i) => (
                          <li key={i} className="whitespace-pre-wrap">
                            {i === painted.chosen ? "✓ " : "  "}
                            {c.title} — {c.emblem}; {c.medium}
                          </li>
                        ))}
                      </ul>
                    </li>
                  )}
                  {painted.designerNotes && <li className="text-fg-muted">Designer's notes: {painted.designerNotes}</li>}
                </>
              )}
              <li>
                Chat: {thousands(usage.inputTokens)} in · {thousands(usage.outputTokens)} out{m.model ? ` · ${m.model}` : ""}
              </li>
              <li>
                Image: 1 · {img.size.replace("x", "×")} · {img.quality} · {img.model}
                {img.costUsd != null ? ` · ${costText(img.costUsd)}` : ""}
              </li>
              {/* A paint whose inherited type read badly on the new plate: what the check changed. */}
              {m.typography?.legibility && <li>{legibilityLine(m.typography.legibility)}</li>}
              {usage.costUsd != null && <li>Cost: {costText(usage.costUsd)} (chat, concepts and the image)</li>}
            </>
          ) : (
            <li>
              Tokens: {thousands(usage.inputTokens)} in · {thousands(usage.outputTokens)} out
              {usage.costUsd != null ? ` · ${costText(usage.costUsd)}` : ""}
              {m.model ? ` · ${m.model}` : ""}
            </li>
          )}
        </ul>
      </details>
    </div>
  );
}

/** "Legibility: title Lc 78 · author Lc 64 · scrim raised to strong" (no last part when the check changed nothing). */
function legibilityLine(l: Legibility): string {
  return `Legibility: title ${l.title} · author ${l.author}${l.changed ? ` · ${l.changed}` : ""}`;
}

function TypographyAnswer({ m, t, tokens, version }: { m: Message; t: Typography; tokens: number; version: ReactNode }) {
  const usage = m.usage ?? { inputTokens: 0, outputTokens: 0 };
  return (
    <div className="flex max-w-[85%] flex-col gap-6 self-start">
      <div className="rounded-14 bg-panel px-14 py-12 text-15 leading-[1.5] whitespace-pre-wrap text-fg">{m.text}</div>
      {m.fallbackUsed && <div className="px-4 text-13 text-fg-muted">{fallbackNote(m.fallbackUsed)}</div>}
      <details className="px-4 text-13 text-fg-muted">
        <summary className="cursor-pointer">
          {version}
          Typography · Details · {tokensAndCost(tokens, usage)}
        </summary>
        <ul className="m-0 mt-6 flex list-none flex-col gap-4 p-0 font-mono text-12">
          <li>
            font {t.fontFamily ?? t.font}
            {t.requestedFont ? ` · asked “${t.requestedFont}”` : ""}
            {t.requestedFont && t.substituted ? " · substituted: not an open font here" : ""}
          </li>
          <li>
            title {t.titleSize} {t.titlePlace} {t.align} {t.titleCaps ? "caps" : "as typed"} · author {t.authorPlace} {t.authorSize} · {t.color} · {t.scrim} · no image
          </li>
          {t.legibility && <li>{legibilityLine(t.legibility)}</li>}
          <li>
            Chat: {thousands(usage.inputTokens)} in · {thousands(usage.outputTokens)} out
            {usage.costUsd != null ? ` · ${costText(usage.costUsd)}` : ""}
            {m.model ? ` · ${m.model}` : ""}
          </li>
        </ul>
      </details>
    </div>
  );
}

// `container-type: size` makes the cover the reference for CoverType's cqh/cqw units.
const surface = "relative box-border aspect-[2/3] h-[60vh] max-h-[60vh] max-w-full overflow-hidden rounded-6 bg-panel text-fg md:h-full md:max-h-580";
const sized: CSSProperties = { containerType: "size" };

/** "Artistic license" + a segmented radiogroup (the license mockup): container radius 10, 1px outline, 3px padding, near-black;
 * buttons 36px, radius 7, selected = accent fill, black 600; others transparent, white 400. The note under it. Below md
 * the buttons' side padding is 10px so the control stays on one line at 390. */
function LicenseControl({ value, onChange, disabled }: { value: License; onChange: (l: License) => void; disabled: boolean }) {
  const current = licenses.find((l) => l.id === value) ?? licenses[1];
  // Off with the input; the kit's disabled tokens (selected = panel fill, labels fg-faint, divider border).
  const look = (on: boolean) =>
    disabled
      ? `cursor-not-allowed ${on ? "bg-panel font-semibold text-fg-faint" : "bg-transparent font-normal text-fg-faint"}`
      : `cursor-pointer ${on ? "bg-accent font-semibold text-on-accent" : "bg-transparent font-normal text-fg"}`;
  return (
    <div className="flex shrink-0 flex-col items-center gap-8 px-24 pt-16">
      <div role="radiogroup" aria-label="Artistic license" className="flex flex-wrap items-center justify-center gap-x-12 gap-y-6">
        <span className="text-13 text-fg-muted">Artistic license</span>
        <div className={`flex rounded-10 border p-3 whitespace-nowrap ${disabled ? "border-divider" : "border-outline"}`} style={{ background: "#0B0B0B" }}>
          {licenses.map((l) => {
            const on = l.id === value;
            return (
              <button
                key={l.id}
                type="button"
                role="radio"
                aria-checked={on}
                disabled={disabled}
                aria-disabled={disabled || undefined}
                onClick={() => onChange(l.id)}
                className={`min-h-36 rounded-[7px] border-0 px-10 font-sans text-14 md:px-16 ${look(on)}`}
              >
                {l.name}
              </button>
            );
          })}
        </div>
      </div>
      <span className="max-w-560 text-center text-13 text-fg-muted">{current.note}</span>
    </div>
  );
}
const ring: CSSProperties = { boxShadow: "0 0 0 1px var(--abp-divider)", ...sized };

/** "Versions", a 44×66 thumbnail per version (selected: accent ring), a divider, then "Download vN" (the versions mockup).
 * A thumbnail is the cover with its type, by the same rules at its own size: two type versions of one plate differ. */
function VersionsStrip({ sessionId, versions, selected, book, onSelect }: { sessionId: string; versions: Version[]; selected: Version; book: Book; onSelect: (n: number) => void }) {
  const title = book.title;
  return (
    <div className="flex shrink-0 flex-wrap items-center justify-center gap-10 px-24 pb-20">
      <span className="mr-6 text-13 text-fg-muted">Versions</span>
      {versions.map((v) => (
        <button
          key={v.n}
          type="button"
          aria-label={`Version ${v.n}`}
          aria-pressed={v.n === selected.n}
          onClick={() => onSelect(v.n)}
          className="relative h-66 w-44 cursor-pointer overflow-hidden rounded-3 border-0 bg-panel p-0"
          style={{ outline: `2px solid ${v.n === selected.n ? "var(--abp-accent)" : "transparent"}`, outlineOffset: "2px", ...sized }}
        >
          <img src={v.imageDataUrl} alt="" className="block h-full w-full object-cover" />
          <span aria-hidden="true">
            <CoverType book={book} typography={v.typography} />
          </span>
          <span className="absolute right-3 bottom-2 font-sans text-11 font-bold text-fg" style={{ textShadow: "0 1px 4px rgba(0,0,0,0.9)" }}>
            {v.n}
          </span>
        </button>
      ))}
      <span className="mx-8 h-32 w-1 bg-divider" />
      <a
        href={downloadUrl(sessionId, selected.n)}
        download={`${slug(title)}-v${selected.n}.png`}
        className="box-border inline-flex min-h-40 items-center gap-8 rounded-8 border border-outline px-14 text-14 text-fg no-underline"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M12 3v12" />
          <path d="m7 10 5 5 5-5" />
          <path d="M5 21h14" />
        </svg>
        Download v{selected.n}
      </a>
    </div>
  );
}

export default function DesignScreen({ onBack }: { onBack: () => void }) {
  const { sessionId = "" } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [available, setAvailable] = useState<boolean | null>(null);
  const [imagesAvailable, setImagesAvailable] = useState<boolean | null>(null);
  const [book, setBook] = useState<Book | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  // The level the next chat carries; loaded from the session, so a reload restores the last level sent.
  const [license, setLicense] = useState<License>("interpretive");
  // A chat request is in flight and could paint (images configured). The api answers only when the turn is done, so
  // a paint can't be told from plain thinking before then.
  const [painting, setPainting] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  // The chat models (picker), the one the next turn runs on, the member's allowance, and the signed-out state.
  const [models, setModels] = useState<ChatModel[]>([]);
  const [model, setModel] = useState<string | null>(null);
  const [budget, setBudget] = useState<Budget | null>(null);
  const [signedOut, setSignedOut] = useState(false);
  // Re-renders when a used-up allowance's reset time passes (the input comes back by itself).
  const [clock, setClock] = useState(() => Date.now());
  const list = useRef<HTMLDivElement>(null);

  // ?v=N when N is a version; otherwise (absent or unknown) the latest.
  const asked = Number(params.get("v"));
  const selected = versions.find((v) => v.n === asked) ?? versions.at(-1) ?? null;
  const select = (n: number) => navigate({ search: `?v=${n}` }); // push: Back/Forward move through the versions

  // Load (or resume after a reload) the session the URL names. Sessions live in api memory: an unknown id → /.
  useEffect(() => {
    let current = true;
    Promise.all([availability(), loadSession(sessionId)])
      .then(([a, s]) => {
        if (!current) return;
        if (!s) return navigate("/", { replace: true });
        setAvailable(a.chat);
        setImagesAvailable(a.images);
        setBook({ title: s.title, author: s.author });
        setVersions(s.versions);
        setLicense(s.license);
        setModel(s.model);
        setBudget(s.budget);
        setMsgs(fromSession(s.messages));
      })
      .catch((e) => {
        if (!current) return;
        if (e instanceof SignedOutError) return setSignedOut(true);
        setMsgs([{ kind: "error", text: errorText(e) }]);
      });
    return () => {
      current = false;
    };
  }, [sessionId, navigate]);

  // The models to pick from (the picker shows only with a choice) and the allowance, once chat is available.
  const refreshModels = () =>
    modelsAndBudget().then((d) => {
      setModels(d.chatModels);
      setModel((m) => m ?? d.chatModels.find((x) => x.isDefault)?.id ?? null);
      setBudget((b) => d.budget ?? b);
      return d.chatModels;
    });
  useEffect(() => {
    if (available !== true) return;
    refreshModels().catch((e) => e instanceof SignedOutError && setSignedOut(true));
  }, [available]);

  // The session's model is unavailable (the site tripped its provider): move to the default, with a note.
  useEffect(() => {
    const current = models.find((x) => x.id === model);
    if (!current || current.available !== false) return;
    const to = fallbackModel(models);
    const text = to
      ? modelSwitchNote(modelLabel(current), modelLabel(to))
      : `${modelLabel(current)} is unavailable right now, and no other model is. Try again later.`;
    setMsgs((prev) => [...prev, { kind: "note", text }]);
    if (to) setModel(to.id);
  }, [models, model]);

  // The allowance again when the tab gets the focus back: it may have been changed on the site meanwhile. Behind the
  // site only; standalone nothing is read.
  useEffect(() => {
    if (!gatewayMode || available !== true) return;
    return onAttention(
      () =>
        void loadBudget()
          .then((b) => setBudget((was) => b ?? was))
          .catch(() => undefined),
    );
  }, [available]);

  // Today's allowance under the input (behind the site): normal, low, used up or off. A used-up allowance comes back at
  // its reset time: re-render then, and ask the site for the new balance. Off has no reset and no timer.
  const now = new Date(clock);
  const allowance = gatewayMode ? allowanceView(budget, { now }) : null;
  const exhausted = holdsOff(allowance, now);
  // The used-up or off notice sits above the input, outside the scrolling messages, so it is always in view.
  const noticeShown = exhausted && !!allowance?.notice && !signedOut;
  const untilMs = exhausted ? (allowance?.until?.getTime() ?? null) : null;
  useEffect(() => {
    if (untilMs === null) return;
    const timer = setTimeout(() => {
      setClock(Date.now());
      if (available === true) refreshModels().catch(() => undefined);
    }, Math.max(0, untilMs - Date.now()) + 250);
    return () => clearTimeout(timer);
  }, [untilMs]);

  // The browser tab reads "{title} · Book Cover" while a design is open, and what it read before once it closes.
  const title = book?.title;
  useEffect(() => {
    if (!title) return;
    const was = document.title;
    document.title = `${title} · Book Cover`;
    return () => {
      document.title = was;
    };
  }, [title]);

  // A block body: newer browsers return a Promise from scrollTo, and an effect may only return a cleanup function.
  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight });
  }, [msgs, sending]);

  // A turn's answer; its failed paint is the note under it (paintProblem; an older API's IMAGE_ERROR or CONCEPT_ERROR is
  // an error bubble). A new version is shown at once: with ?v=N in the URL it is replaced by the new N; without, the
  // latest is shown anyway.
  const show = (t: Turn) => {
    const answer = t.session.messages.filter((m) => m.role === "designer").at(-1);
    const failed: Msg[] = t.imageError && !answer?.paintProblem ? [{ kind: "error", text: errorText(t.imageError) }] : [];
    setMsgs((prev) => [...prev, ...(answer ? [{ kind: "answer" as const, message: answer }] : []), ...failed]);
    const newest = t.session.versions.at(-1);
    if (newest && newest.n > (versions.at(-1)?.n ?? 0) && params.has("v")) navigate({ search: `?v=${newest.n}` }, { replace: true });
    setVersions(t.session.versions);
    if (t.session.budget) setBudget(t.session.budget);
  };

  // A failed turn (problemView): signed out, used up and off are states of the panel (a notice, the input off); a
  // model the site refused moves the session to another; the rest are bubbles, with Try again when sending the same
  // message again could help.
  const failed = (e: unknown, message: string, attempt: number) => {
    if (e instanceof SignedOutError) return setSignedOut(true);
    const view = problemView(problemOf(e instanceof GraphQLError ? e : undefined), errorText(e), { gateway: gatewayMode, attempt });
    if (view.kind === "signed-out") return setSignedOut(true);
    if (view.kind === "budget" || view.kind === "chat-off") {
      setClock(Date.now());
      return setBudget((was) => refusedBudget(view.kind === "budget" ? "budget" : "chat-off", was, view.until, view.dailyUsd));
    }
    if (view.kind === "model") {
      // The site's list again; if it still lists this model as available, it is not (the site just said so).
      refreshModels()
        .then((list) => setModels(list.map((x) => (x.id === model ? { ...x, available: false } : x))))
        .catch(() => undefined);
      return;
    }
    setMsgs((prev) => [...prev, { kind: "error", text: view.text, again: view.retry === "button" ? message : undefined }]);
  };

  // `again`: Try again / Retry paint / the automatic second send after a rate limit (no new "mine" bubble for the first
  // two when nothing of the failed turn was kept). attempt 1 = the automatic second send.
  const send = (message: string, opts: { again?: boolean; attempt?: number } = {}) => {
    const m = message.trim();
    const attempt = opts.attempt ?? 0;
    if (!m || (sending && attempt === 0) || !available || !book || exhausted || signedOut) return;
    if (attempt === 0) {
      setText("");
      setSending(true);
      setPainting(imagesAvailable === true);
      if (!opts.again) setMsgs((prev) => [...prev, { kind: "mine", text: m }]);
    }
    const done = () => {
      setSending(false);
      setPainting(false);
    };
    // The api lost the session while this screen was open (UNKNOWN_SESSION, e.g. it restarted): start a new one for
    // the same title and author, send once more, and move the URL to the new session.
    chat(sessionId, m, license, selected?.n, model ?? undefined)
      .catch((e) => {
        if (!(e instanceof GraphQLError && e.code === "UNKNOWN_SESSION")) throw e;
        return startSession(book, license).then((t) =>
          chat(t.session.id, m, license, undefined, model ?? undefined).then((r) => {
            navigate(`/design/${t.session.id}`, { replace: true });
            return r;
          }),
        );
      })
      .then((t) => {
        show(t);
        done();
      })
      .catch((e) => {
        const view = problemView(problemOf(e instanceof GraphQLError ? e : undefined), errorText(e), { gateway: gatewayMode, attempt });
        if (view.retry === "auto") {
          // A rate limit: "One moment…", then the same message once more after the wait; still sending meanwhile.
          setMsgs((prev) => [...prev, { kind: "note", text: rateWaitText }]);
          setTimeout(() => send(m, { again: true, attempt: 1 }), view.retryAfterMs ?? 5000);
          return;
        }
        failed(e, m, attempt);
        done();
      });
  };

  const disabled = available !== true || exhausted || signedOut;
  // The site's sign-in page, to come back to this design afterwards.
  const signIn = signInUrl(siteUrl, window.location.href);
  // After a version exists: "Try a different font." when the latest turn painted, "Try something bolder." otherwise.
  const lastAnswer = msgs.findLast((x): x is Extract<Msg, { kind: "answer" }> => x.kind === "answer")?.message;
  const tryText = !versions.length ? firstTry : lastAnswer?.image ? fontTry : nextTry;
  // Retry paint: on the latest answer only, re-sending the author's message of that turn.
  const lastAnswerAt = msgs.findLastIndex((x) => x.kind === "answer");
  const askedAt = msgs.slice(0, lastAnswerAt).findLastIndex((x) => x.kind === "mine");
  const lastAsked = askedAt >= 0 ? (msgs[askedAt] as Extract<Msg, { kind: "mine" }>).text : null;

  return (
    <div className="flex min-h-screen flex-col md:h-screen">
      <AppTopBar book={book?.title ?? ""} onChangeBook={onBack} />

      <div className="flex grow flex-col md:min-h-0 md:flex-row">
        <main className="flex min-w-0 grow flex-col">
          {book && <LicenseControl value={license} onChange={setLicense} disabled={disabled || sending} />}
          <div className={`flex grow items-center justify-center px-24 pt-24 md:min-h-0 ${selected ? "pb-12" : "pb-24"}`}>
            {!book ? null : selected ? (
              // painted: the selected plate fills the surface; the scrim and the shadowed type lie over it.
              <div aria-label={`Book cover, version ${selected.n}`} className={surface} style={ring}>
                <img src={selected.imageDataUrl} alt="" className="absolute inset-0 block h-full w-full object-cover" />
                <CoverType book={book} typography={selected.typography} />
              </div>
            ) : painting ? (
              // painting: the type at 50% and the note in the middle.
              <div aria-label="Book cover" aria-busy="true" className={surface} style={ring}>
                <CoverType book={book} typography={defaultTypography} scrim={false} faded />
                <span className="absolute inset-x-0 top-1/2 -translate-y-1/2 px-24 text-center text-14 text-fg-muted">Painting… this takes up to a minute</span>
              </div>
            ) : (
              <div aria-label="Book cover" className={surface} style={ring}>
                <CoverType book={book} typography={defaultTypography} scrim={false} />
              </div>
            )}
          </div>
          {book && selected && <VersionsStrip sessionId={sessionId} versions={versions} selected={selected} book={book} onSelect={select} />}
        </main>

        <section
          aria-label="Chat with the cover designer"
          className="flex flex-col border-t border-divider md:min-h-0 md:w-380 md:shrink-0 md:border-t-0 md:border-l"
        >
          <div className="flex shrink-0 items-center gap-12 border-b border-divider p-16">
            <div className="flex h-40 w-40 items-center justify-center rounded-999 bg-accent font-bold text-on-accent">CD</div>
            <div className="flex min-w-0 grow flex-col">
              <span className="text-16 font-bold">Cover designer</span>
              <span className="text-13 text-fg-muted">{book?.title}</span>
              {/* The model picker (the gateway mockup): 28px, max 220px, 13px; the next turn runs on the pick, the versions stay. */}
              {showModelPicker(models) && model && (
                <select
                  aria-label="Model"
                  value={model}
                  disabled={sending || disabled}
                  onChange={(e) => setModel(e.target.value)}
                  className="mt-4 box-border min-h-28 max-w-[220px] rounded-6 border border-outline bg-page px-8 font-sans text-13 text-fg disabled:cursor-not-allowed disabled:border-divider disabled:text-fg-faint"
                >
                  {/* An unavailable model is greyed out: "GPT-6 Luna — unavailable until 6:15 PM". */}
                  {models.map((x) => (
                    <option key={x.id} value={x.id} disabled={x.available === false}>
                      {modelOptionLabel(x)}
                    </option>
                  ))}
                </select>
              )}
            </div>
          </div>
          <div ref={list} aria-label="Messages" aria-live="polite" className="flex grow flex-col gap-12 p-16 md:min-h-0 md:overflow-y-auto">
            {available === false && <div className="self-center px-8 text-center text-14 text-fg-muted">{noModelNote}</div>}
            {available === true && imagesAvailable === false && <div className="self-center px-8 text-center text-14 text-fg-muted">{noImagesNote}</div>}
            {signedOut && (
              <div role="status" className="self-center px-8 text-center text-14 text-fg-muted">
                {signedOutText}
                {signIn && (
                  <>
                    {" "}
                    <a href={signIn} className="text-accent">
                      Sign in
                    </a>
                  </>
                )}
              </div>
            )}
            {msgs.map((m, i) => (
              <Bubble
                key={i}
                msg={m}
                selected={selected?.n ?? null}
                versions={versions}
                onShow={select}
                onAgain={(again) => send(again, { again: true })}
                onRetryPaint={i === lastAnswerAt && lastAsked ? () => send(lastAsked) : undefined}
                busy={sending || disabled}
              />
            ))}
            {sending && <div className="self-start px-4 text-13 text-fg-muted">Cover designer is {painting ? "painting" : "thinking"}…</div>}
          </div>
          <div className="flex shrink-0 flex-col gap-8 border-t border-divider px-16 pt-12 pb-16">
            {noticeShown && <AllowanceNotice text={allowance!.notice!} />}
            {/* "Try: …" chip — 40px, 0 12px, radius 8, outline, white 14px 400, left-aligned (as TemporalGraph's ChatPanel). */}
            <KitButton
              variant="outline"
              size="sm"
              disabled={disabled || sending}
              onClick={() => send(tryText)}
              style={{ minHeight: "40px", padding: "0 12px", borderRadius: "8px", fontSize: "14px", justifyContent: "flex-start", textAlign: "left" }}
            >
              Try: “{tryText}”
            </KitButton>
            <form
              className="flex gap-8"
              onSubmit={(e) => {
                e.preventDefault();
                send(text);
              }}
            >
              <input
                type="text"
                aria-label="Message"
                placeholder="Say something to the designer…"
                value={text}
                disabled={disabled}
                aria-disabled={disabled || undefined}
                onChange={(e) => setText(e.target.value)}
                className="box-border min-h-44 min-w-0 grow rounded-8 border border-outline bg-panel px-12 font-sans text-15 text-fg disabled:cursor-not-allowed disabled:border-divider disabled:text-fg-faint"
              />
              <KitButton size="form" type="submit" disabled={disabled || sending || text.trim() === ""} style={{ padding: "0 16px" }}>
                Send
              </KitButton>
            </form>
            {allowance && !signedOut && <AllowanceFooter view={allowance} />}
          </div>
        </section>
      </div>
    </div>
  );
}
