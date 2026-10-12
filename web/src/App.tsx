import { useEffect, useRef, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router";
import BookList from "./BookList.tsx";
import DesignScreen from "./DesignScreen.tsx";
import OwnIdeaForm from "./OwnIdeaForm.tsx";
import type { StartFailure, StartState } from "./StartStates.tsx";
import { AppUnavailableError, budget as loadBudget, GraphQLError, SignedOutError, startSession, type BookInput, type Budget } from "./graphql.ts";
import { allowanceView, holdsOff, onAttention, refusedBudget } from "./allowance.ts";
import { problemOf, signedOutText, startView } from "./problems.ts";
import { onSignedOut, startSite, useSite } from "./site.ts";

// The screens as URL paths (react-router, as the other AgentBullpen apps):
//   /                    pick a book
//   /own                 the "Your own book idea" form
//   /design/:sessionId   the design screen; ?v=N selects version N (absent = the latest)
//   anything else        → /
// A card click or the form starts the session (the opening is a model call), then opens /design/<id>. While it starts
// the card or the form reads "Opening…"; a start that failed shows why, with Try again; a member whose allowance is
// used up, or for whom the designer is off, gets a note and the cards and the form off (StartStates.tsx; the texts are
// in problems.ts and allowance.ts).
// Behind the site nothing renders until the site has confirmed the member (site.ts); standalone the app shows at once.
export default function App() {
  const navigate = useNavigate();
  const { pathname } = useLocation();

  // Behind the site, first: who is this (anyone the site doesn't confirm as a member → its sign-in page), then its app
  // status (the under-construction page unless live, or the gateway lets this caller through). Standalone: ready at once.
  const site = useSite();
  const show = site.ready && !site.signedOut;
  useEffect(() => {
    void startSite();
  }, []);

  // One start at a time: a second click while the opening is on its way would start (and pay for) a second session.
  const starting = useRef(false);
  const [opening, setOpening] = useState<BookInput | null>(null);
  const [failure, setFailure] = useState<StartFailure | null>(null);
  // The member's allowance, behind the site: read when the pick screen or the form opens and again when the tab gets
  // the focus back (it may have been changed on the site meanwhile); a start the site refused sets it to used up, or
  // off. `clock` re-renders at the reset time, when the cards come back by themselves. Standalone: never read.
  const [budget, setBudget] = useState<Budget | null>(null);
  const [clock, setClock] = useState(() => Date.now());

  const picking = pathname === "/" || pathname === "/own";
  const reading = show && site.enabled && picking;
  useEffect(() => {
    if (!reading) return;
    const read = () =>
      void loadBudget()
        .then(setBudget)
        .catch(() => {}); // a 401 goes to the sign-in page (graphql.ts); anything else leaves the cards on
    read();
    return onAttention(read);
  }, [reading]);

  const now = new Date(clock);
  const allowance = site.enabled ? allowanceView(budget, { now }) : null;
  const out = holdsOff(allowance, now) ? allowance : null;
  const untilMs = out?.until?.getTime() ?? null;
  useEffect(() => {
    if (untilMs === null) return;
    const timer = setTimeout(() => {
      setClock(Date.now());
      loadBudget()
        .then(setBudget)
        .catch(() => {});
    }, Math.max(0, untilMs - Date.now()) + 250);
    return () => clearTimeout(timer);
  }, [untilMs]);

  // `attempt`: 0 = the author's click (or Try again), 1 = the one automatic second try after a rate limit.
  const start = (book: BookInput, attempt = 0) => {
    if ((starting.current && attempt === 0) || out) return;
    starting.current = true;
    setOpening(book);
    setFailure(null);
    const done = () => {
      starting.current = false;
      setOpening(null);
    };
    startSession(book).then(
      (t) => {
        done();
        navigate(`/design/${t.session.id}`);
      },
      (e: unknown) => {
        // The demo was closed mid-session: the visitor is on the way to the site's under-construction page.
        if (e instanceof AppUnavailableError) return done();
        // A 401: the visitor is on the way to the sign-in page (graphql.ts sent them); without a site address to send
        // them to, the panel says so.
        const noSignIn = () => setFailure({ book, reason: `${signedOutText.charAt(0).toLowerCase()}${signedOutText.slice(1)}.` });
        if (e instanceof SignedOutError) {
          done();
          if (!site.enabled || !site.siteUrl) noSignIn();
          return;
        }
        const view = startView(problemOf(e instanceof GraphQLError ? e : undefined), { gateway: site.enabled, attempt });
        switch (view.kind) {
          case "rate-wait":
            setTimeout(() => start(book, attempt + 1), view.retryAfterMs);
            return;
          case "budget":
          case "chat-off":
            done();
            setClock(Date.now());
            return setBudget((was) => (view.kind === "budget" ? refusedBudget("budget", was, view.until, view.dailyUsd) : refusedBudget("chat-off", was)));
          case "signed-out":
            done();
            if (!onSignedOut()) noSignIn();
            return;
          default:
            done();
            return setFailure({ book, reason: view.reason });
        }
      },
    );
  };

  // Behind the site nothing renders until the site has confirmed the member, and nothing at all for anyone else.
  if (!show) return null;

  const state: StartState = {
    opening,
    failure,
    outOfTokens: out?.notice ?? null,
    onRetry: () => failure && start(failure.book),
  };
  // A book that didn't open is said where it was picked: moving between the pick screen and the form clears it.
  const go = (to: string) => {
    setFailure(null);
    navigate(to);
  };

  return (
    <Routes>
      <Route path="/" element={<BookList onPick={start} onOwnIdea={() => go("/own")} start={state} />} />
      <Route path="/own" element={<OwnIdeaForm onStart={start} onBack={() => go("/")} start={state} />} />
      <Route path="/design/:sessionId" element={<DesignScreen onBack={() => navigate("/")} />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
