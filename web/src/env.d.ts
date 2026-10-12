/// <reference types="vite/client" />

// Build-time settings. Both empty (the default) = standalone; set = behind the AgentBullpen.AI gateway (graphql.ts).
interface ImportMetaEnv {
  /** The site's API base, e.g. https://api.agentbullpen.ai; GraphQL then goes to <base>/covers/graphql. */
  readonly VITE_API_BASE_URL?: string;
  /** The site's address, for the "Sign in" link when the gateway answers 401. */
  readonly VITE_SITE_URL?: string;
}
