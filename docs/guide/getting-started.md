# Getting started

_Guide_

A chat with a cover designer that paints. A fresh clone runs with no secrets; only the designer and the paintings call a model.

![One designer turn](../diagrams/paint-turn.svg)

## Prerequisites

.NET SDK 10 (for the Aspire AppHost), the Aspire CLI 13.6, Node.js 22 or later, and [uv](https://docs.astral.sh/uv/) with Python 3.13 or later (`uv python install 3.13` if `uv python list` shows none).

## A model

Copy the `Llm` block from `src/CoverDesigner.AppHost/appsettings.json` into a new `appsettings.Development.json` beside it (gitignored), fill it in, and store the key:

```
aspire secret set Parameters:llm-api-key <your key>
```

Paintings need an image model too, on OpenAI or OpenRouter: the `Llm:Image` block and `Parameters:image-api-key`. Without a chat model the app runs and says so; without images the designer still talks. Every setting: [Configuration](configuration.md).

## Run it

```
aspire run
```

The dashboard shows `api` (Python, GraphQL at `/graphql`) and `web` (Vite + React). Open `web`.

## The screens

- **Pick a book:** *The Hound of the Baskervilles*, *The Three Musketeers*, *Treasure Island*, or **Your own book idea** — title, author, what it's about and a cover style (five presets, the designer's choice, or your own words).
- **Design:** the cover in the middle, the designer on the right. Ask for art and it paints; ask about lettering and it changes the type without painting. Each painting is a version in the strip under the cover, and so is each type change: a new version of the same picture. Every version has its own URL (`/design/<session>?v=N`). **Download** saves the shown version as a PNG with the type drawn in.
- **Artistic license** above the cover sets how far the next paint may move from the book's own look: [Artistic license](../design/artistic-license.md).

Sessions live in the API's memory: after a restart a design link goes back to the book list. The docs are at `/docs`. To run the API alone as a container: `docker build -t coverdesigner-api api`.
