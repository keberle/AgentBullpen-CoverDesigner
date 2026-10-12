import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { FluentProvider } from "@fluentui/react-components";
import "./design-kit/fonts.ts";
import "./kit.css";
import { buildTheme } from "./design-kit/fluent-theme.ts";
import { BrowserRouter, Route, Routes } from "react-router";
import App from "./App.tsx";

// /docs/* is the docs (its own chunk, loaded on first visit); every other path is the app. The docs sit outside the app, so
// they never run its sign-in check or call the site, and nothing links one to the other.
const Docs = lazy(() => import("./docs/Docs.tsx"));

const theme = buildTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <FluentProvider theme={theme}>
      {/* Classes go on an inner div, not the provider: Fluent copies the provider
          className onto its portal node. Inline 16px / normal beats FluentProvider's
          own 14px / 20px body text: the mockups render at browser defaults. */}
      <div className="min-h-screen bg-page font-sans text-fg" style={{ fontSize: "16px", lineHeight: "normal" }}>
        <BrowserRouter>
          <Routes>
            <Route
              path="/docs/*"
              element={
                <Suspense fallback={null}>
                  <Docs />
                </Suspense>
              }
            />
            <Route path="*" element={<App />} />
          </Routes>
        </BrowserRouter>
      </div>
    </FluentProvider>
  </StrictMode>,
);
