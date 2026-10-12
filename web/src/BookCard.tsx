// "Pick a book" card button with its hero image (the pick mockup): the
// image 3:2, object-fit cover, on top; title and author below, 20/24/24 padding.
export type Hero = { src768: string; src1536: string; alt: string };

// The grid (max 1080px, columns minmax(300px, 1fr), gap 20, page padding 24) gives 3, 2 or 1 columns.
const sizes = "(min-width: 1128px) 347px, (min-width: 988px) calc((100vw - 88px) / 3), (min-width: 668px) calc((100vw - 68px) / 2), calc(100vw - 48px)";

type Props = {
  title: string;
  author: string;
  hero?: Hero;
  lazy?: boolean;
  /** This book's session is starting: a dimmed overlay reading "Opening…". */
  opening?: boolean;
  /** The allowance is used up, or the designer is off: dimmed, not clickable. */
  disabled?: boolean;
  onClick: () => void;
};

export function BookCard({ title, author, hero, lazy, opening, disabled, onClick }: Props) {
  return (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      aria-disabled={disabled || undefined}
      className={`relative flex flex-col overflow-hidden rounded-16 border border-divider bg-panel p-0 text-left font-sans text-fg ${disabled ? "cursor-not-allowed opacity-45" : "cursor-pointer"}`}
    >
      {hero && (
        <img
          src={hero.src768}
          srcSet={`${hero.src768} 768w, ${hero.src1536} 1536w`}
          sizes={sizes}
          alt={hero.alt}
          loading={lazy ? "lazy" : undefined}
          className="block aspect-[3/2] w-full object-cover"
        />
      )}
      <span className="flex flex-col gap-6 px-24 pt-20 pb-24">
        <span className="text-22 leading-[1.2] font-bold">{title}</span>
        <span className="text-15 text-fg-muted">{author}</span>
      </span>
      {opening && (
        <span role="status" className="absolute inset-0 flex items-center justify-center bg-[rgba(0,0,0,0.6)] text-15 font-semibold">
          Opening…
        </span>
      )}
    </button>
  );
}
