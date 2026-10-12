// The docs' table of contents: the three groups of docs/README.md, each page in its table's order. `label` is the
// page's link text in README.md (menu and pager), `title` its H1 (the browser tab), `file` its path under docs/. toc.test.ts
// checks this list against README.md and the disk, so adding a page is: write it, add it to README.md's table, add its line here.

export type GroupKey = "guide" | "design" | "story";

export type DocPage = { group: GroupKey; slug: string; label: string; title: string; file: string };

export type DocGroup = { key: GroupKey; name: string; eyebrow: string; pages: DocPage[] };

const page = (group: GroupKey, slug: string, label: string, title = label): DocPage => ({
  group,
  slug,
  label,
  title,
  file: `${group}/${slug}.md`,
});

export const groups: readonly DocGroup[] = [
  {
    key: "guide",
    name: "Guide",
    eyebrow: "GUIDE",
    pages: [
      page("guide", "getting-started", "Getting started"),
      page("guide", "configuration", "Configuration"),
      page("guide", "gateway", "Behind a gateway"),
      page("guide", "project-layout", "Project layout"),
    ],
  },
  {
    key: "design",
    name: "Design & research",
    eyebrow: "DESIGN & RESEARCH",
    pages: [
      page("design", "designer", "The designer"),
      page("design", "artistic-license", "Artistic license"),
      page("design", "cover-craft", "Cover craft"),
      page("design", "typography", "Typography and download"),
      page("design", "models", "Models and gateway"),
      page("design", "errors", "Errors"),
    ],
  },
  {
    key: "story",
    name: "The story",
    eyebrow: "THE STORY",
    pages: [page("story", "ux-first", "UX first")],
  },
];

export const allPages: readonly DocPage[] = groups.flatMap((g) => g.pages);

/** The landing page, /docs. */
export const indexFile = "README.md";

export const pagePath = (p: DocPage) => `/docs/${p.group}/${p.slug}`;

export function findPage(group: string | undefined, slug: string | undefined): DocPage | null {
  return allPages.find((p) => p.group === group && p.slug === slug) ?? null;
}

export function pageByFile(file: string): DocPage | null {
  return allPages.find((p) => p.file === file) ?? null;
}

/** The eyebrow over the H1: the group and the page's place in it, "Design & research · 1 of 6". */
export function eyebrow(p: DocPage): string {
  const g = groups.find((x) => x.key === p.group)!;
  return `${g.name} · ${g.pages.indexOf(p) + 1} of ${g.pages.length}`;
}

/** Previous and next in reading order: through the page's group, then on into the next one (as the approved page shows). */
export function neighbours(p: DocPage): { prev: DocPage | null; next: DocPage | null } {
  const i = allPages.indexOf(p);
  return { prev: allPages[i - 1] ?? null, next: allPages[i + 1] ?? null };
}
