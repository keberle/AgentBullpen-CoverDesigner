# Notice

The code in this repository is under the [MIT License](LICENSE). Some files are not code, or come from elsewhere, and carry their own terms:

## Fonts

`api/fonts/<family>/` holds seventeen typefaces from Google's [`google/fonts`](https://github.com/google/fonts) repository (commit `51303ca`), each under the [SIL Open Font License 1.1](https://openfontlicense.org), with its `OFL.txt` beside it:

| Folder | Typeface |
|---|---|
| `api/fonts/alfaslabone/` | Alfa Slab One |
| `api/fonts/anton/` | Anton |
| `api/fonts/bangers/` | Bangers |
| `api/fonts/bebasneue/` | Bebas Neue |
| `api/fonts/cinzel/` | Cinzel |
| `api/fonts/comicneue/` | Comic Neue |
| `api/fonts/cormorantgaramond/` | Cormorant Garamond |
| `api/fonts/courierprime/` | Courier Prime |
| `api/fonts/creepster/` | Creepster |
| `api/fonts/greatvibes/` | Great Vibes |
| `api/fonts/montserrat/` | Montserrat |
| `api/fonts/opensans/` | Open Sans |
| `api/fonts/orbitron/` | Orbitron |
| `api/fonts/oswald/` | Oswald |
| `api/fonts/playfairdisplay/` | Playfair Display |
| `api/fonts/rye/` | Rye |
| `api/fonts/unifrakturmaguntia/` | UnifrakturMaguntia |

The web app's copies of the same typefaces come from Fontsource packages restored by npm; they are not in this repository.

## Images

The book hero images (`web/src/assets/book-*.webp`) were generated for this project with OpenAI's GPT-Image-2.5 and converted to WebP. OpenAI's Services Agreement (§4.1) assigns ownership of the output to the customer. Covers the app paints at run time are not part of the repository, except one kept small as a test plate (`api/tests/fixtures/hound-plate.jpg`), painted by this app the same way. The README screenshot (`media/`) is of this app.

## Books

The three demo books (*The Hound of the Baskervilles*, *The Three Musketeers* and *Treasure Island*) are public-domain titles. The repository holds only their titles, authors and short style notes (`api/styles/`), none of their text.

## Packages

Packages restored by uv, NuGet and npm (for example Microsoft Agent Framework, FastAPI, Strawberry, Pillow and Fluent UI) are not in this repository and come under their own licences.
