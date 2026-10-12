import { KitButton } from "./design-kit/components/KitButton.tsx";
import { TopBar } from "./design-kit/components/TopBar.tsx";
import { SiteLinks, SiteUserMenu, SiteWordmark } from "./SiteChrome.tsx";
import { useSite } from "./site.ts";

// The top bar of every screen (pick, own idea, design). Left: the wordmark and "/ Book Cover"; behind the site the wordmark
// links to the site and "Other demos" follows. Right: behind the site the signed-in member's user menu; standalone the
// "no sign-in" note. Standalone the site parts render nothing, so the bar is the wordmark, the name and the note.
// Behind the site, at phone width, the name gives way: the wordmark, Other demos and the menu fill the 64px bar's one row.
//
// The design screen passes its book: the name reads "/ Book Cover · {title}" and a Change book button follows it in the
// same group, as on AgentBullpen-TemporalGraph's book screen. The button shows at every width. So that the 64px bar stays
// one row, the name (cut short with an ellipsis when the title is long) gives way below 768px, and below 640px so do
// Other demos and the "no sign-in" note.
export function AppTopBar({ book, onChangeBook }: { book?: string; onChangeBook?: () => void }) {
  const site = useSite();
  const design = book !== undefined;
  const name = design
    ? "min-w-0 max-w-[calc(100vw-520px)] truncate text-15 text-fg-muted max-md:hidden"
    : `text-15 whitespace-nowrap text-fg-muted ${site.enabled ? "max-sm:hidden" : ""}`.trim();
  return (
    // The mockups' header never shrinks (flex-shrink: 0); the kit TopBar has no class for it.
    <div className="shrink-0">
      <TopBar
        left={
          <>
            <div className={design ? "flex min-w-0 items-center gap-16" : "flex items-center gap-16"}>
              <SiteWordmark />
              <span className={name}>{book ? `/ Book Cover · ${book}` : "/ Book Cover"}</span>
              {design && onChangeBook && (
                <KitButton variant="outline" size="xs" onClick={onChangeBook} className="shrink-0">
                  Change book
                </KitButton>
              )}
            </div>
            {design ? (
              // No box of its own (display: contents), so standalone, with no links, it adds no gap to the bar.
              <div className="contents max-sm:hidden">
                <SiteLinks />
              </div>
            ) : (
              <SiteLinks />
            )}
          </>
        }
        right={
          site.enabled ? <SiteUserMenu /> : <span className={`text-13 text-fg-muted ${design ? "whitespace-nowrap max-sm:hidden" : ""}`.trim()}>Standalone · no sign-in</span>
        }
      />
    </div>
  );
}
