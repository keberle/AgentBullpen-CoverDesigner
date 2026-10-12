# Models and gateway

_Design & research_

Four model calls make a cover: the intent call, the designer's chat, the concept call and the image. The same build runs standalone from a clone or behind the AgentBullpen.AI site, which holds the keys.

![One build, two modes](../diagrams/gateway.svg)

## Which model does what

| Call | Standalone | Behind the site (defaults) |
|---|---|---|
| The designer's chat | `LLM_MODEL` | `GATEWAY_CHAT_MODEL`, or the member's pick |
| The concept call | `LLM_CONCEPT_MODEL`, else `LLM_MODEL` | `GATEWAY_CONCEPT_MODEL` |
| The image | `IMAGE_MODEL` | `GATEWAY_IMAGE_MODEL` |

Chat and image providers are independent: Anthropic has no image model, so a mix is normal. The image call speaks OpenAI's Images API or OpenRouter's (`POST …/images`); behind the site it goes through OpenRouter like every other call.

## What a turn costs

Every author message starts with the intent call, on the chat model. A type change adds a designer turn; a paint adds a designer turn, a concept call and an image. Measuring contrast calls no model.

## One protocol

Every OpenAI-compatible chat call — OpenAI, Azure OpenAI, OpenRouter and the site's proxy routes — goes over Chat Completions, and the API keeps each session's conversation itself. Nothing is stored at the provider, so switching models mid-session keeps the conversation and every version. Anthropic uses its own client.

## Two modes, four adapters

One switch per side chooses the mode at startup. Four small adapters in `api/modes.py` hold the whole difference — the current user, model calls, the model catalog and the allowance — so the designer's code has no `if gateway`. Behind the site every call carries the member's token as its key to the site's LLM proxy, which meters it; standalone the configured keys are used and a cost is shown only when the provider reports one. A half-filled gateway configuration stops the API at startup, so a site deployment never runs open.

## Telemetry

The API exports OpenTelemetry to the Aspire dashboard: a span per model call and download, and a count of problems by type.
