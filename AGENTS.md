# Notes for coding agents

For an agent (Claude Code, Codex, …) working in this repo on its user's behalf.

## Set up this repo for someone

Only the cover designer needs a model; the book list runs without configuration.

1. Check the prerequisites: .NET SDK 10 (`global.json`), Aspire CLI 13.6.0, Node.js 22 or later, and [uv](https://docs.astral.sh/uv/getting-started/installation/) with Python 3.13 or later (`uv python install 3.13` installs one).
2. Ask the user (unless they already said) which provider they want (OpenAI, Anthropic, Azure OpenAI or OpenRouter) and which model. For Azure, also ask for the endpoint (`https://<resource>/openai/v1/`); the model is the deployment's name.
3. Put the chat model settings in a new `src/CoverDesigner.AppHost/appsettings.Development.json`: copy the `Llm` block from `src/CoverDesigner.AppHost/appsettings.json` and fill in `Type`, `Model` and `Endpoint` (what each one means: [Configuration](docs/guide/configuration.md)). The file is gitignored. It holds no key.
4. The key goes into the AppHost's secret parameter `llm-api-key`, which is kept in the user-secrets store. Either:
   - the user runs `aspire secret set Parameters:llm-api-key <their key>` themselves, or
   - the user puts the key in an environment variable **themselves**, where your commands can see it (for example, before starting you: `export OPENAI_API_KEY=…` or `$env:OPENAI_API_KEY = "…"`), and you run `aspire secret set Parameters:llm-api-key "$OPENAI_API_KEY"` (PowerShell: `aspire secret set Parameters:llm-api-key $env:OPENAI_API_KEY`).
   - **Never ask them to paste the key to you.**
   - **Never put a key on a command line as a literal.**
   - **Never print a key**, and don't echo the variable to check it.
5. Images are optional, on OpenAI or OpenRouter: fill in the `Llm:Image` block in the same file, and set `Parameters:image-api-key` the same way as in step 4.
6. Run `aspire run`. The web app's URL is on the Aspire dashboard, under the `web` resource. If the app was already running, stop it with `aspire stop` first.
7. Check it with the `availability` query, a POST to `/graphql` on the web app's URL:
   ```
   { availability { chat chatNote images imagesNote } }
   ```
   `chat: true` means the designer is ready. Otherwise `chatNote` and `imagesNote` name the settings that are missing.

**To undo:** delete `src/CoverDesigner.AppHost/appsettings.Development.json`, and ask the user to remove the two secrets from the AppHost's user-secrets.

## Build and test

```
dotnet build
cd api && uv run pytest
cd web && npm ci && npm test && npm run build
```

The api's container image: `docker build -t coverdesigner-api api`. It holds no settings; they come from environment variables at run time.

## Rules

- **Never print or commit a key.** Keys live only in the AppHost's user-secrets. The repo is public.
- **Gitignored local files:** `appsettings.Development.json` and `.archive/` stay out of commits.
- **The tests make no network calls** and no model calls. Keep it that way.
