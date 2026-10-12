// The model picker's rules on the design screen, copied from AgentBullpen-TemporalGraph's web/src/chat.ts. The allowance
// under the input is in allowance.ts; the texts for the failure states are in problems.ts (problemView).
import type { ChatModel } from "./graphql.ts";

/** The picker shows only when there is a choice. */
export function showModelPicker(models: ChatModel[]): boolean {
  return models.length > 1;
}

/** The picker's label: the display name when the API has one, else the model id. */
export function modelLabel(m: ChatModel): string {
  return m.displayName?.trim() || m.id;
}

/** The label of the model with this id; the id itself if it is not in the list. */
export function modelLabelOf(models: ChatModel[], id: string | null): string {
  const m = models.find((x) => x.id === id);
  return m ? modelLabel(m) : (id ?? "");
}
