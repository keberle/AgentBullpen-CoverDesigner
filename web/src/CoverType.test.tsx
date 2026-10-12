// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import { CoverType, defaultTypography, faces } from "./CoverType.tsx";

// The library on the web side: the same seventeen faces, weights and title tracking as the api's (api/typography.py).
describe("The cover's faces", () => {
  it("are the library's seventeen, in its order", () => {
    expect(Object.keys(faces)).toEqual([
      "oswald", "anton", "bebas", "playfair", "cormorant", "cinzel", "opensans", "montserrat", "bangers", "comicneue", "creepster",
      "unifraktur", "rye", "alfaslab", "orbitron", "greatvibes", "courierprime",
    ]);
  });

  it("have the api's title weights; the author is 600 in a variable family, else the face's one weight", () => {
    const title = Object.fromEntries(Object.entries(faces).map(([id, f]) => [id, f.title]));
    expect(title).toEqual({
      oswald: 700, anton: 400, bebas: 400, playfair: 800, cormorant: 700, cinzel: 800, opensans: 800, montserrat: 800, bangers: 400,
      comicneue: 700, creepster: 400, unifraktur: 400, rye: 400, alfaslab: 400, orbitron: 800, greatvibes: 400, courierprime: 700,
    });
    for (const [id, f] of Object.entries(faces)) expect([id, f.author]).toEqual([id, f.family.includes("Variable") ? 600 : f.title]);
  });

  it("keep the first four faces' title tracking; the others have none", () => {
    const tracked = Object.fromEntries(Object.entries(faces).filter(([, f]) => f.tracking !== "0").map(([id, f]) => [id, f.tracking]));
    expect(tracked).toEqual({ oswald: "-0.014em", playfair: "-0.014em", cinzel: "0.028em", opensans: "-0.014em" });
  });

  it("set the author line as typed in the script and the blackletter face, in capitals elsewhere", () => {
    expect(Object.entries(faces).filter(([, f]) => f.authorCaps === false).map(([id]) => id)).toEqual(["unifraktur", "greatvibes"]);
  });

  it("never set the title in capitals in those two faces, whatever the setting says", async () => {
    expect(Object.entries(faces).filter(([, f]) => f.caps === false).map(([id]) => id)).toEqual(["unifraktur", "greatvibes"]);
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const book = { title: "The Hound of the Baskervilles", author: "Arthur Conan Doyle" };
    const drawn = async (font: string) => {
      const container = document.createElement("div");
      const root = createRoot(container);
      await act(async () => root.render(createElement(CoverType, { book, typography: { ...defaultTypography, font, titleCaps: true } })));
      const text = container.textContent ?? "";
      await act(async () => root.unmount());
      return text;
    };
    expect(await drawn("greatvibes")).toBe("The Hound of the BaskervillesArthur Conan Doyle");
    expect(await drawn("unifraktur")).toBe("The Hound of the BaskervillesArthur Conan Doyle");
    expect(await drawn("bangers")).toBe("THE HOUND OF THE BASKERVILLESARTHUR CONAN DOYLE");
  });

  it("load on demand, all but the app's own font", () => {
    expect(Object.entries(faces).filter(([, f]) => !f.load).map(([id]) => id)).toEqual(["opensans"]);
  });
});
