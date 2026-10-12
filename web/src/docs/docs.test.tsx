import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { MDXProvider } from "@mdx-js/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { DocLink, docComponents, Sheet } from "./components.tsx";
import { resolveLink } from "./links.ts";
import { loaders } from "./pages.ts";
import { rehypeDocs } from "./rehypeDocs.ts";
import { allPages, indexFile } from "./toc.ts";

const html = (node: ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);

describe("the a component", () => {
  it("turns a relative .md link into the page's route, keeping the #anchor", () => {
    expect(resolveLink("configuration.md", "guide/getting-started.md")).toEqual({ kind: "route", to: "/docs/guide/configuration" });
    expect(resolveLink("../design/designer.md#two-tools", "story/ux-first.md")).toEqual({ kind: "route", to: "/docs/design/designer#two-tools" });
    expect(resolveLink("design/artistic-license.md", "README.md")).toEqual({ kind: "route", to: "/docs/design/artistic-license" });
    expect(resolveLink("../README.md", "guide/configuration.md")).toEqual({ kind: "route", to: "/docs" });
    expect(html(<DocLink href="configuration.md" file="guide/getting-started.md">Configuration</DocLink>)).toMatch(
      /^<a [^>]*href="\/docs\/guide\/configuration"[^>]*>Configuration<\/a>$/,
    );
  });

  it("opens http(s) links in a new tab", () => {
    const a = html(<DocLink href="https://www.rfc-editor.org/rfc/rfc9457" file="design/errors.md">RFC 9457</DocLink>);
    expect(a).toContain('href="https://www.rfc-editor.org/rfc/rfc9457"');
    expect(a).toContain('target="_blank"');
    expect(a).toContain('rel="noreferrer"');
  });

  it("keeps an in-page #anchor as it is", () => {
    expect(resolveLink("#two-tools", "design/designer.md")).toEqual({ kind: "anchor", href: "#two-tools" });
    const a = html(<DocLink href="#two-tools" file="design/designer.md">Two tools</DocLink>);
    expect(a).toContain('href="#two-tools"');
    expect(a).not.toContain("target");
  });

  it("reports a page that doesn't exist, and renders it as text, not a dead link", () => {
    expect(resolveLink("nope.md", "guide/configuration.md").kind).toBe("missing");
    expect(resolveLink("../../README.md", "guide/configuration.md").kind).toBe("missing"); // outside docs/
    expect(html(<DocLink href="nope.md" file="guide/configuration.md">Nope</DocLink>)).toBe("<span>Nope</span>");
  });
});

describe("the img component", () => {
  it("puts a sheet in its frame with the alt text as the caption", () => {
    const fig = html(<Sheet src="../diagrams/paint-turn.svg" alt="One designer turn" file="design/designer.md" />);
    expect(fig).toMatch(/<img src="[^"]*paint-turn[^"]*\.svg" alt="One designer turn"/);
    expect(fig).toContain("min-w-[758px]");
    expect(fig).toContain("<figcaption");
  });

  it("says so when a sheet is missing", () => {
    expect(html(<Sheet src="../diagrams/nope.svg" alt="x" file="design/designer.md" />)).toContain("Missing sheet");
  });
});

describe("the Note box", () => {
  it("renders a blockquote with the Note label", () => {
    const Quote = docComponents("design/designer.md").blockquote as (p: { children: ReactNode }) => ReactNode;
    const box = html(<Quote>{<p>the author's request beats the book's style block</p>}</Quote>);
    expect(box).toContain(">Note</span>");
    expect(box).toContain("border-outline");
  });
});

async function render(file: string) {
  const { default: Content } = await loaders[file]();
  return html(
    <MDXProvider components={docComponents(file)}>
      <Content />
    </MDXProvider>,
  );
}

describe("every page compiles and renders", () => {
  it.each([indexFile, ...allPages.map((p) => p.file)])("%s", async (file) => {
    const out = await render(file);
    expect(out).toMatch(/^(<link [^>]*\/>)*<h1 id="[^"]+"/); // React hoists a preload <link> for each image to the front
    expect(out).not.toContain("Missing sheet");
    expect(out).not.toMatch(/<em>(The story|Guide|Design &amp; research)( · |<)/); // said once, by the eyebrow
  });

  // The pages' line under the H1 is the group alone ("_Design & research_"), with no sources.
  it("gives The designer the sheet frame, and leaves out the line that only repeats the eyebrow", async () => {
    const out = await render("design/designer.md");
    expect(out).not.toContain("Design &amp; research</em>");
    expect(out).toMatch(/<\/h1>\s*<p[^>]*>[^<]*<\/p>\s*<figure/); // the title, the page's lead, then the sheet
    expect(out).not.toMatch(/<p[^>]*>\s*<figure/); // the image paragraph is unwrapped
    expect(out).toContain(">Note</span>"); // its blockquote is the Note box
    expect(await render("story/ux-first.md")).not.toMatch(/<em>The story<\/em>/);
    expect(await render("guide/gateway.md")).not.toMatch(/<em>Guide<\/em>/);
  });

  it("keeps a sources line, without the group in front of it", () => {
    type N = { type: string; tagName?: string; value?: string; properties?: Record<string, unknown>; children?: N[] };
    const el = (tagName: string, ...children: N[]): N => ({ type: "element", tagName, properties: {}, children });
    const tree = el("root", el("h1", { type: "text", value: "UX first" }), el("p", el("em", { type: "text", value: "The story · 2 of 6 · sources: the design notes" })));
    rehypeDocs()(tree);
    const line = tree.children![1];
    expect(line.properties!.className).toEqual(["doc-sources"]);
    expect(line.children![0].children![0].value).toBe("Sources: the design notes");
  });

  it("puts tables in a scrolling box", async () => {
    expect(await render("guide/configuration.md")).toMatch(/<div class="overflow-x-auto[^"]*"><table/);
  });

  // No page carries an "Open", "Still open" or "Not built yet" section any more; the panel plugin stays for the rehype unit test.
  it("renders no outlined Open panel on any page", async () => {
    for (const file of ["design/designer.md", "design/models.md", "design/errors.md", "story/ux-first.md"]) {
      expect(await render(file), file).not.toContain("<section");
    }
  });

  it("keeps a < in prose as text", async () => {
    // typography.md has `<session>` in code and gateway.md `<placeholder>`; format "md" never parses markup, so they reach the
    // page as text.
    expect(await render("design/typography.md")).toContain("/download/&lt;session&gt;/&lt;n&gt;");
    expect(await render("guide/gateway.md")).toContain("&lt;placeholder&gt;");
  });
});
