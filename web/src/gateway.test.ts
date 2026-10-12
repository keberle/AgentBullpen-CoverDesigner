// AgentBullpen-TemporalGraph's gateway tests (web/src/chat.test.ts, "models, budget and the gateway"), for the
// copied helpers; the texts say "designer" where TemporalGraph's say "chat", and the gateway path is /covers.
import { describe, expect, it } from "vitest";
import { modelLabel, modelLabelOf, showModelPicker } from "./gateway.ts";
import { downloadUrl, graphqlUrl, type ChatModel } from "./graphql.ts";

describe("models, budget and the gateway", () => {
  const model = (id: string, isDefault = false): ChatModel => ({ id, displayName: id, provider: "openai", isDefault });

  it("shows the picker only when there is a choice", () => {
    expect(showModelPicker([model("openai:gpt-4o-mini", true)])).toBe(false);
    expect(showModelPicker([model("openai:gpt-4o-mini", true), model("openai:gpt-6-luna")])).toBe(true);
  });

  it("labels a model by its display name, else its id", () => {
    expect(modelLabel({ ...model("openai:gpt-6-luna"), displayName: "GPT-6 Luna" })).toBe("GPT-6 Luna");
    expect(modelLabel({ ...model("openai:gpt-6-luna"), displayName: "  " })).toBe("openai:gpt-6-luna");
    const list = [{ ...model("a"), displayName: "Model A" }];
    expect(modelLabelOf(list, "a")).toBe("Model A");
    expect(modelLabelOf(list, "gone")).toBe("gone");
    expect(modelLabelOf(list, null)).toBe("");
  });

  it("goes to /graphql standalone and to the gateway's /covers/graphql otherwise", () => {
    expect(graphqlUrl("")).toBe("/graphql");
    expect(graphqlUrl("https://api.agentbullpen.ai")).toBe("https://api.agentbullpen.ai/covers/graphql");
    expect(downloadUrl("abc", 2, "")).toBe("/download/abc/2");
    expect(downloadUrl("abc", 2, "https://api.agentbullpen.ai")).toBe("https://api.agentbullpen.ai/covers/download/abc/2");
  });
});
