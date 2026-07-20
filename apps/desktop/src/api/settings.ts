import { apiWithConfig, type AppConfig } from "@/api/core";
import type {
  ModelsResponse,
  ServerConfig,
  ToolMetadata,
  ToolOverrideDecision,
  ToolPolicyMetadata,
} from "@/api/types";

// ─── MCP servers ──────────────────────────────────────────────────────

export type MCPTransport = "stdio" | "http";

export interface MCPTool {
  name: string;
  full_name: string;
  description: string;
  enabled: boolean;
  policy: ToolPolicyMetadata;
  override?: ToolOverrideDecision;
}

export interface MCPServer {
  name: string;
  transport: MCPTransport | "unknown";
  connected: boolean;
  tool_count: number;
  error: string | null;
  command: string | null;
  args: string[] | null;
  url: string | null;
  tools: MCPTool[];
  enabled: boolean;
  auth: string | null;
  header_keys?: string[];
  has_client_credentials: boolean;
  client_id?: string | null;
  redirect_port?: number | null;
  scope?: string | null;
  client_name?: string | null;
  has_client_secret?: boolean;
}

/** Raw payload shape persisted on the server. The desktop reads/writes this
 *  dict directly via add/update; the server validates it through
 *  parse_server_config. */
export interface MCPServerConfigPayload {
  transport: MCPTransport;
  enabled?: boolean;
  // stdio
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  // http
  url?: string;
  headers?: Record<string, string>;
  auth?: string;
  client_id?: string;
  client_secret?: string;
  redirect_port?: number;
  scope?: string;
  client_name?: string;
  // common
  tools?: string[] | null;
}

export async function listMCPServersApi(config: AppConfig): Promise<{ servers: MCPServer[] }> {
  return apiWithConfig(config, "/mcp/servers");
}

export async function addMCPServerApi(
  config: AppConfig,
  name: string,
  serverConfig: MCPServerConfigPayload,
): Promise<void> {
  await apiWithConfig(config, "/mcp/servers", {
    method: "POST",
    body: JSON.stringify({ name, config: serverConfig }),
  });
}

export async function updateMCPServerApi(
  config: AppConfig,
  name: string,
  serverConfig: MCPServerConfigPayload,
): Promise<void> {
  await apiWithConfig(config, `/mcp/servers/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify({ config: serverConfig }),
  });
}

export async function updateMCPToolsApi(
  config: AppConfig,
  name: string,
  tools: string[] | null,
): Promise<void> {
  await apiWithConfig(config, `/mcp/servers/${encodeURIComponent(name)}/tools`, {
    method: "PUT",
    body: JSON.stringify({ tools }),
  });
}

export async function toggleMCPServerApi(
  config: AppConfig,
  name: string,
  enabled: boolean,
): Promise<void> {
  await apiWithConfig(config, `/mcp/servers/${encodeURIComponent(name)}/enabled`, {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}

export async function startMCPOAuthApi(config: AppConfig, name: string): Promise<void> {
  await apiWithConfig(config, `/mcp/servers/${encodeURIComponent(name)}/oauth`, {
    method: "POST",
  });
}

export async function removeMCPServerApi(config: AppConfig, name: string): Promise<void> {
  await apiWithConfig(config, `/mcp/servers/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}

export interface CustomModelSummary {
  id: string;
  base_url: string | null;
  context_window: number;
}

export interface ModelProvider {
  id: string;
  name: string;
  connected: boolean;
  key_hint: string | null;
  from_env: boolean;
  auth_type?: "api_key" | "oauth";
  model_count?: number;
  models: string[] | CustomModelSummary[];
  embedding_models: string[];
}

export interface OpenAICodexOAuthStart {
  status: string;
  url: string;
  opened: boolean;
  expires_at: number;
  instructions?: string;
}

export interface OpenAICodexOAuthStatus {
  connected: boolean;
  status: string;
  account_id?: string | null;
  expires?: number;
  error?: string | null;
  url?: string;
  opened?: boolean;
  expires_at?: number;
}

export async function listModelProvidersApi(config: AppConfig): Promise<ModelProvider[]> {
  const r = await apiWithConfig<{ providers: ModelProvider[] }>(config, "/providers");
  return r.providers;
}

export async function connectModelProviderApi(
  config: AppConfig,
  providerId: string,
  apiKey: string,
  chatModel?: string | null,
): Promise<void> {
  await apiWithConfig(config, `/providers/${encodeURIComponent(providerId)}/connect`, {
    method: "POST",
    body: JSON.stringify({ api_key: apiKey, chat_model: chatModel || undefined }),
  });
}

export async function disconnectModelProviderApi(config: AppConfig, providerId: string): Promise<void> {
  await apiWithConfig(config, `/providers/${encodeURIComponent(providerId)}`, { method: "DELETE" });
}

export async function startOpenAICodexOAuthApi(config: AppConfig): Promise<OpenAICodexOAuthStart> {
  return apiWithConfig<OpenAICodexOAuthStart>(config, "/providers/openai-codex/oauth/browser/start", {
    method: "POST",
  });
}

export async function getOpenAICodexOAuthStatusApi(config: AppConfig): Promise<OpenAICodexOAuthStatus> {
  return apiWithConfig<OpenAICodexOAuthStatus>(config, "/providers/openai-codex/oauth/status");
}

export interface ServiceConnection {
  id: string;
  name: string;
  connected: boolean;
  key_hint: string | null;
  from_env: boolean;
}

export interface GmailAccount {
  email: string | null;
  token_file: string;
  has_send_scope?: boolean;
  error?: string;
}

export type GoogleIntegrationId = "gmail" | "calendar" | "google_drive";

export interface GoogleAccountSummary {
  id: string;
  email: string | null;
  services: GoogleIntegrationId[];
  scopes: string[];
  error?: string | null;
}

// ─── Setup assistant contracts ─────────────────────────────────────────

export type GoogleServiceChoice = "email" | "email_calendar" | "calendar" | "all";

export interface GoogleCredentialsStatus {
  path: string;
  exists: boolean;
  valid: boolean;
  client_id: string | null;
  client_type: string | null;
  error: string | null;
}

export interface ToolProviderConnection {
  id: string;
  label: string;
  kind: "native" | "mcp";
  status: "connected" | "error" | "not_configured";
  detail: string | null;
  tool_count: number;
}

export interface CalendarTokenStatus {
  token_file: string;
  has_calendar_scope: boolean;
  error?: string | null;
}

export interface SetupStatus {
  google: {
    enabled: boolean;
    credentials: GoogleCredentialsStatus;
    accounts: GmailAccount[];
    google_accounts: GoogleAccountSummary[];
    calendar_tokens: CalendarTokenStatus[];
    provider_statuses: ToolProviderConnection[];
  };
  slack: {
    services: ServiceConnection[];
    provider_status: ToolProviderConnection | null;
  };
  mcp: {
    servers: MCPServer[];
    provider_statuses: ToolProviderConnection[];
  };
}

export interface GooglePreflightResponse {
  ok: boolean;
  credentials: GoogleCredentialsStatus;
  scopes: string[];
  warnings: string[];
}

export interface SlackVerifyResponse {
  ok: boolean;
  token_kind: "bot" | "user";
  team?: string | null;
  team_id?: string | null;
  user?: string | null;
  bot_id?: string | null;
}

export interface CreateCustomModelPayload {
  model_id: string;
  base_url: string;
  context_window: number;
  max_output_tokens: number;
  api_key?: string | null;
}

export async function listServicesApi(config: AppConfig): Promise<ServiceConnection[]> {
  const r = await apiWithConfig<{ services: ServiceConnection[] }>(config, "/services");
  return r.services;
}

export async function connectServiceApi(config: AppConfig, serviceId: string, apiKey: string): Promise<void> {
  await apiWithConfig(config, `/services/${encodeURIComponent(serviceId)}/connect`, {
    method: "POST",
    body: JSON.stringify({ api_key: apiKey }),
  });
}

export async function disconnectServiceApi(config: AppConfig, serviceId: string): Promise<void> {
  await apiWithConfig(config, `/services/${encodeURIComponent(serviceId)}`, { method: "DELETE" });
}

export async function listGmailAccountsApi(config: AppConfig): Promise<GmailAccount[]> {
  const r = await apiWithConfig<{ accounts: GmailAccount[] }>(config, "/gmail/accounts");
  return r.accounts;
}

export async function listGoogleAccountsApi(config: AppConfig): Promise<GoogleAccountSummary[]> {
  const response = await apiWithConfig<{ accounts: GoogleAccountSummary[] }>(config, "/google/accounts");
  return response.accounts;
}

export async function connectGoogleServiceApi(
  config: AppConfig,
  integrationId: GoogleIntegrationId,
  accountId?: string,
): Promise<{ status: string; account: GoogleAccountSummary }> {
  return apiWithConfig(config, `/google/${integrationId}/connect`, {
    method: "POST",
    body: JSON.stringify(accountId ? { account_id: accountId } : {}),
  });
}

export async function disconnectGoogleServiceApi(
  config: AppConfig,
  integrationId: GoogleIntegrationId,
  accountId: string,
): Promise<void> {
  await apiWithConfig(config, `/google/${integrationId}/accounts/${encodeURIComponent(accountId)}`, {
    method: "DELETE",
  });
}

export async function removeGoogleAccountApi(config: AppConfig, accountId: string): Promise<void> {
  await apiWithConfig(config, `/google/accounts/${encodeURIComponent(accountId)}`, { method: "DELETE" });
}

export async function getSetupStatusApi(config: AppConfig): Promise<SetupStatus> {
  return apiWithConfig<SetupStatus>(config, "/setup/status");
}

export async function saveGoogleCredentialsApi(
  config: AppConfig,
  payload: { path?: string; json?: unknown },
): Promise<{ status: string; credentials: GoogleCredentialsStatus }> {
  return apiWithConfig<{ status: string; credentials: GoogleCredentialsStatus }>(config, "/setup/google/credentials", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function preflightGoogleSetupApi(
  config: AppConfig,
  integrationId: GoogleIntegrationId,
): Promise<GooglePreflightResponse> {
  return apiWithConfig<GooglePreflightResponse>(config, "/setup/google/preflight", {
    method: "POST",
    body: JSON.stringify({ integration_id: integrationId }),
  });
}

export async function addGmailAccountApi(
  config: AppConfig,
  serviceChoice: GoogleServiceChoice = "all",
): Promise<{ email: string | null; status: string; token_file?: string; scopes?: string[] }> {
  return apiWithConfig<{ email: string | null; status: string; token_file?: string; scopes?: string[] }>(config, "/gmail/add", {
    method: "POST",
    body: JSON.stringify({ service_choice: serviceChoice }),
  });
}

export async function verifySlackTokenApi(
  config: AppConfig,
  serviceId: "slack_bot_token" | "slack_user_token",
  apiKey: string,
): Promise<SlackVerifyResponse> {
  return apiWithConfig<SlackVerifyResponse>(config, "/setup/slack/verify", {
    method: "POST",
    body: JSON.stringify({ service_id: serviceId, api_key: apiKey }),
  });
}

export async function removeGmailAccountApi(
  config: AppConfig,
  tokenFile: string,
): Promise<{ email: string | null; status: string }> {
  return apiWithConfig<{ email: string | null; status: string }>(
    config,
    `/gmail/${encodeURIComponent(tokenFile)}`,
    { method: "DELETE" },
  );
}

export async function createCustomModelApi(
  config: AppConfig,
  payload: CreateCustomModelPayload,
): Promise<{ status: string; model_id: string }> {
  return apiWithConfig<{ status: string; model_id: string }>(config, "/models/custom", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteCustomModelApi(
  config: AppConfig,
  modelId: string,
): Promise<{ status: string; model_id: string }> {
  return apiWithConfig<{ status: string; model_id: string }>(
    config,
    `/models/custom/${encodeURIComponent(modelId)}`,
    { method: "DELETE" },
  );
}

export async function getServerConfig(config: AppConfig): Promise<ServerConfig> {
  return parseServerConfig(await apiWithConfig<unknown>(config, "/config"));
}

export async function getServerModels(config: AppConfig): Promise<ModelsResponse> {
  return parseModelsResponse(await apiWithConfig<unknown>(config, "/models"));
}

export type ServerConfigPatch = Partial<{
  chat_model: string;
  research_model: string;
  workflow_model: string;
  memory_model: string;
  max_depth: number;
  reasoning_model: string;
  reasoning_effort: string | null;
  compression_threshold: number;
  max_messages: number;
  compression_keep_ratio: number;
  summary_max_tokens: number;
  consolidation_interval: number;
  web_search: "auto" | "exa" | "ddgs" | "none";
  tool_overrides: Record<string, ToolOverrideDecision>;
  integrations: Partial<Record<GoogleIntegrationId, boolean | null>> & {
    google?: boolean | null;
    memory?: boolean | null;
  };
}>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isStringRecord(value: unknown): value is Record<string, string> {
  return isRecord(value) && Object.values(value).every((item) => typeof item === "string");
}

function isToolOverrideRecord(value: unknown): value is Record<string, ToolOverrideDecision> {
  return (
    isRecord(value) &&
    Object.values(value).every((item) => item === "approve" || item === "ask" || item === "deny")
  );
}

function isStringArrayRecord(value: unknown): value is Record<string, string[]> {
  return isRecord(value) && Object.values(value).every(isStringArray);
}

function assertServerContract(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

export function parseServerConfig(data: unknown): ServerConfig {
  assertServerContract(isRecord(data), "Invalid /config response: expected an object");
  assertServerContract(typeof data.chat_model === "string", "Invalid /config response: missing chat_model");
  assertServerContract(typeof data.research_model === "string", "Invalid /config response: missing research_model");
  assertServerContract(typeof data.memory_model === "string", "Invalid /config response: missing memory_model");
  assertServerContract(isStringArray(data.reasoning_efforts), "Invalid /config response: missing reasoning_efforts");
  assertServerContract(
    isStringRecord(data.model_reasoning_efforts),
    "Invalid /config response: missing model_reasoning_efforts",
  );
  assertServerContract(
    typeof data.compaction_token_limit === "number",
    "Invalid /config response: missing compaction_token_limit",
  );
  assertServerContract(
    typeof data.compaction_token_trigger === "number",
    "Invalid /config response: missing compaction_token_trigger",
  );
  assertServerContract(
    data.tool_overrides === undefined || isToolOverrideRecord(data.tool_overrides),
    "Invalid /config response: invalid tool_overrides",
  );
  if (data.tool_overrides === undefined) data.tool_overrides = {};
  return data as unknown as ServerConfig;
}

export function parseModelsResponse(data: unknown): ModelsResponse {
  assertServerContract(isRecord(data), "Invalid /models response: expected an object");
  assertServerContract(isStringArray(data.models), "Invalid /models response: missing models");
  assertServerContract(Array.isArray(data.groups), "Invalid /models response: missing groups");
  assertServerContract(
    isStringArrayRecord(data.reasoning_efforts),
    "Invalid /models response: missing reasoning_efforts",
  );
  return data as unknown as ModelsResponse;
}

export async function patchServerConfig(
  config: AppConfig,
  patch: ServerConfigPatch,
): Promise<ServerConfig> {
  const data = await apiWithConfig<unknown>(config, "/config", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return parseServerConfig(data);
}

export async function listToolsApi(config: AppConfig): Promise<{ tools: ToolMetadata[] }> {
  return apiWithConfig(config, "/tools");
}
