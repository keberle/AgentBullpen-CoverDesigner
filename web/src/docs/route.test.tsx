// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

// The docs route itself, in a browser-like page: public (no sign-in check and no call to the site or the api, also in a
// gateway build), its own top bar, the menu, the "group · n of m" line, the pager, and no link to the app in either direction.

vi.setConfig({ testTimeout: 60_000 });

async function mount(path: string, gateway: boolean) {
  vi.resetModules();
  if (gateway) {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.agentbullpen.test");
    vi.stubEnv("VITE_SITE_URL", "https://agentbullpen.test");
  }
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.scrollTo = () => {};
  const calls: string[] = [];
  vi.stubGlobal("fetch", async (url: string) => {
    calls.push(String(url));
    return new Response("", { status: 401 });
  });
  const { act, createElement, Suspense } = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { MemoryRouter, Route, Routes } = await import("react-router");
  const { default: Docs } = await import("./Docs.tsx");
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      createElement(MemoryRouter, { initialEntries: [path] },
        createElement(Routes, null,
          createElement(Route, { path: "/docs/*", element: createElement(Suspense, { fallback: null }, createElement(Docs)) }),
          createElement(Route, { path: "*", element: "the app" }))),
    );
  });
  // The page is its own lazy chunk: wait for its H1.
  for (let i = 0; i < 200 && !container.querySelector("main h1"); i++) await act(async () => new Promise((r) => setTimeout(r, 10)));
  return { container, calls, unmount: () => act(async () => root.unmount()) };
}

let page: Awaited<ReturnType<typeof mount>> | null = null;

afterEach(async () => {
  await page?.unmount();
  page = null;
  document.body.innerHTML = "";
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("the docs route", () => {
  it("shows a page with its bar, menu, place in its group and pager", async () => {
    page = await mount("/docs/design/designer", false);
    const { container } = page;
    expect(container.querySelector("header")!.textContent).toBe("AgentBullpen.AI/ Book Cover · Docs");
    expect(container.querySelector("main h1")!.textContent).toBe("The designer");
    expect(container.querySelector("main > span")!.textContent).toBe("Design & research · 1 of 6");
    expect(document.title).toBe("The designer · Book Cover docs");
    const menu = container.querySelector('nav[aria-label="Docs"]')!;
    expect([...menu.querySelectorAll("span")].map((s) => s.textContent)).toEqual(["GUIDE", "DESIGN & RESEARCH", "THE STORY"]);
    expect([...menu.querySelectorAll("a")].map((a) => a.textContent)).toEqual([
      "Getting started", "Configuration", "Behind a gateway", "Project layout",
      "The designer", "Artistic license", "Cover craft", "Typography and download", "Models and gateway", "Errors",
      "UX first",
    ]);
    expect(menu.querySelector('[aria-current="page"]')!.textContent).toBe("The designer");
    const pager = container.querySelector('nav[aria-label="Pager"]')!;
    expect([...pager.querySelectorAll("a")].map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["← Project layout", "/docs/guide/project-layout"],
      ["Next: Artistic license →", "/docs/design/artistic-license"],
    ]);
    // The sheet, in its bordered frame that scrolls sideways.
    const figure = container.querySelector("main figure")!;
    expect(figure.querySelector("div")!.className).toMatch(/overflow-x-auto.*border/);
    expect(figure.querySelector("img")!.getAttribute("src")).toMatch(/paint-turn.*\.svg/);
  });

  it("is public: no sign-in check and no call to anything, also in a gateway build", async () => {
    for (const gateway of [true, false]) {
      page = await mount("/docs", gateway);
      expect(page.container.querySelector("main h1")!.textContent).toBe("AgentBullpen Cover Designer — docs");
      expect(page.calls).toEqual([]);
      const header = page.container.querySelector("header")!;
      expect(header.querySelector("button")).toBeNull(); // no user menu, no Other demos
      expect(header.textContent).not.toMatch(/Standalone|Other demos/);
      // The wordmark is plain; the bar's one link is "Docs", to /docs.
      expect([...header.querySelectorAll("a")].map((a) => a.getAttribute("href"))).toEqual(["/docs"]);
      await page.unmount();
      page = null;
    }
  });

  it("links only within the docs or out to the web: never to the app", async () => {
    for (const path of ["/docs", "/docs/guide/getting-started", "/docs/design/errors", "/docs/story/ux-first"]) {
      page = await mount(path, false);
      const hrefs = [...page.container.querySelectorAll("a")].map((a) => a.getAttribute("href")!);
      expect(hrefs.length).toBeGreaterThan(5);
      for (const href of hrefs) expect(href, `${path} → ${href}`).toMatch(/^(\/docs(\/|#|$)|https?:\/\/|#)/);
      await page.unmount();
      page = null;
    }
  });

  it("says so at an address the docs don't have", async () => {
    page = await mount("/docs/design/nope", false);
    expect(page.container.querySelector("main h1")!.textContent).toBe("No such page");
    expect(document.title).toBe("Not found · Book Cover docs");
  });
});

describe("the app", () => {
  it("has no link to the docs", async () => {
    vi.resetModules();
    const { renderToStaticMarkup } = await import("react-dom/server");
    const { createElement } = await import("react");
    const { MemoryRouter } = await import("react-router");
    const { default: BookList } = await import("../BookList.tsx");
    const { default: OwnIdeaForm } = await import("../OwnIdeaForm.tsx");
    const html =
      renderToStaticMarkup(createElement(MemoryRouter, null, createElement(BookList, { onPick: () => {}, onOwnIdea: () => {} }))) +
      renderToStaticMarkup(createElement(MemoryRouter, null, createElement(OwnIdeaForm, { onStart: () => {}, onBack: () => {} })));
    expect(html).toContain("Pick a book");
    expect(html).not.toMatch(/\/docs|>Docs</);
  });
});
