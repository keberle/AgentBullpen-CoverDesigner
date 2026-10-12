// Aspire AppHost — `aspire run` starts the GraphQL API and the React app.
//
//   api   (Python: FastAPI + Strawberry GraphQL under Uvicorn; book list, Cover designer chat)
//   web   (React + Vite + TypeScript + design kit)

var builder = DistributedApplication.CreateBuilder(args);

// WithUv() runs `uv sync` before start, so a fresh copy needs no Python setup.
// The Uvicorn endpoint ("http") keeps port 5301 so it doesn't clash with the
// other AgentBullpen apps.
var api = builder.AddUvicornApp("api", "../../api", "main:app")
    .WithUv()
    .WithEndpoint("http", e => e.Port = 5301)
    .WithHttpHealthCheck("/health")
    .WithExternalHttpEndpoints();

// The chat model (docs/guide/configuration.md). Non-secret settings come from the
// Llm section (appsettings.json template, overridden by a gitignored appsettings.Development.json);
// the key is the secret parameter llm-api-key, added only when it has a value, so the app
// starts without it and the api reports chat as unavailable.
var llm = builder.Configuration.GetSection("Llm");
api.WithEnvironment("LLM_TYPE", llm["Type"] ?? "")
    .WithEnvironment("LLM_MODEL", llm["Model"] ?? "")
    .WithEnvironment("LLM_ENDPOINT", llm["Endpoint"] ?? "")
    .WithEnvironment("LLM_TIMEOUT_SECONDS", llm["TimeoutSeconds"] ?? "")
    // The concept call before every paint (optional): its own model on the same Type, Endpoint and key; empty = Model.
    .WithEnvironment("LLM_CONCEPT_MODEL", llm["Concept:Model"] ?? "")
    .WithEnvironment("LLM_CONCEPT_REASONING_EFFORT", llm["Concept:ReasoningEffort"] ?? "");
if (!string.IsNullOrEmpty(builder.Configuration["Parameters:llm-api-key"]))
{
    api.WithEnvironment("LLM_API_KEY", builder.AddParameter("llm-api-key", secret: true));
}

// The image model (docs/guide/configuration.md): the Llm:Image section and
// the secret parameter image-api-key, added only when it has a value, as above.
var image = llm.GetSection("Image");
api.WithEnvironment("IMAGE_TYPE", image["Type"] ?? "")
    .WithEnvironment("IMAGE_MODEL", image["Model"] ?? "")
    .WithEnvironment("IMAGE_ENDPOINT", image["Endpoint"] ?? "")
    .WithEnvironment("IMAGE_SIZE", image["Size"] ?? "")
    .WithEnvironment("IMAGE_QUALITY", image["Quality"] ?? "")
    .WithEnvironment("IMAGE_TIMEOUT_SECONDS", image["TimeoutSeconds"] ?? "");
if (!string.IsNullOrEmpty(builder.Configuration["Parameters:image-api-key"]))
{
    api.WithEnvironment("IMAGE_API_KEY", builder.AddParameter("image-api-key", secret: true));
}

// Gateway mode (docs/guide/gateway.md): behind the AgentBullpen.AI site the api validates the site's JWT and
// sends every model call through the site's LLM proxy. Off unless a signing key is set, in Auth:Gateway:SigningKey or as
// the secret parameter gateway-signing-key (added only when it has a value, like llm-api-key). Empty = standalone.
var gateway = builder.Configuration.GetSection("Auth:Gateway");
var gatewayKey = gateway["SigningKey"] ?? "";
if (!string.IsNullOrEmpty(builder.Configuration["Parameters:gateway-signing-key"]))
{
    api.WithEnvironment("GATEWAY_SIGNING_KEY", builder.AddParameter("gateway-signing-key", secret: true));
}
else
{
    api.WithEnvironment("GATEWAY_SIGNING_KEY", gatewayKey);
}
api.WithEnvironment("GATEWAY_ISSUER", gateway["Issuer"] ?? "")
    .WithEnvironment("GATEWAY_AUDIENCE", gateway["Audience"] ?? "")
    .WithEnvironment("GATEWAY_LLM_BASE_URL", builder.Configuration["Gateway:LlmBaseUrl"] ?? "")
    .WithEnvironment("GATEWAY_CHAT_MODEL", builder.Configuration["Gateway:ChatModel"] ?? "")
    .WithEnvironment("GATEWAY_CONCEPT_MODEL", builder.Configuration["Gateway:ConceptModel"] ?? "")
    .WithEnvironment("GATEWAY_IMAGE_MODEL", builder.Configuration["Gateway:ImageModel"] ?? "");

// The browser only talks to the Vite dev server; Vite proxies /graphql and /download to the
// api (target read from API_HTTP, see web/vite.config.ts). Web:ApiBaseUrl / Web:SiteUrl (empty = standalone) build
// the web in gateway mode: it then calls <ApiBaseUrl>/covers/graphql with the member's cookie instead.
builder.AddViteApp("web", "../../web")
    .WithReference(api)
    .WaitFor(api)
    .WithEnvironment("API_HTTP", api.GetEndpoint("http"))
    .WithEnvironment("VITE_API_BASE_URL", builder.Configuration["Web:ApiBaseUrl"] ?? "")
    .WithEnvironment("VITE_SITE_URL", builder.Configuration["Web:SiteUrl"] ?? "")
    .WithExternalHttpEndpoints();

builder.Build().Run();
