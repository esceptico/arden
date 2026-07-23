export interface AppConfig {
  serverUrl: string;
  apiKey: string;
}

export interface ApiBridgeResponse {
  ok: boolean;
  status: number;
  statusText: string;
  contentType: string;
  data: unknown;
  text: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly data: unknown;
  readonly responseText: string;

  constructor(response: { status: number; data?: unknown; text?: string }) {
    super(errorMessageFromResponse(response));
    this.name = "ApiError";
    this.status = response.status;
    this.data = response.data ?? null;
    this.responseText = response.text ?? "";
  }
}

export interface HealthCheck {
  ok: boolean;
  version: string | null;
  hasProviders: boolean;
}

export const STORAGE_KEY = "arden.desktop.config";
export const DEFAULT_API_TIMEOUT_MS = 60_000;

export const DEFAULT_CONFIG: AppConfig = {
  serverUrl: "http://localhost:6877",
  apiKey: "",
};

export function normalizeConfig(config: Partial<AppConfig> | null | undefined): AppConfig {
  return {
    serverUrl: config?.serverUrl?.trim().replace(/\/$/, "") || DEFAULT_CONFIG.serverUrl,
    apiKey: config?.apiKey?.trim() ?? "",
  };
}

export function hostFromUrl(value: string): string {
  try {
    return new URL(value).host || value;
  } catch {
    return value;
  }
}

export function headersForConfig(config: AppConfig, json = false): HeadersInit {
  const out: Record<string, string> = {};
  if (json) out["Content-Type"] = "application/json";
  if (config.apiKey) out.Authorization = `Bearer ${config.apiKey}`;
  return out;
}

export function errorMessageFromResponse(response: { status: number; data?: unknown; text?: string }): string {
  let message = `HTTP ${response.status}`;
  if (response.data && typeof response.data === "object") {
    const body = response.data as { detail?: unknown; message?: unknown };
    if (typeof body.detail === "string") message = body.detail;
    if (typeof body.message === "string") message = body.message;
  } else if (response.text) {
    message = response.text;
  }
  return message;
}

export async function apiWithConfig<T>(config: AppConfig, path: string, init: RequestInit = {}): Promise<T> {
  const { timeout, ...requestInit } = init as RequestInit & { timeout?: number };
  const effectiveTimeout = timeout ?? DEFAULT_API_TIMEOUT_MS;
  const body = typeof requestInit.body === "string" ? requestInit.body : undefined;
  const desktopApi = window.ardenDesktop?.api;

  if (desktopApi) {
    const request = {
      path,
      method: requestInit.method ?? "GET",
      body,
      timeout: effectiveTimeout,
    };
    const response: ApiBridgeResponse = await desktopRequestWithTimeout(desktopApi, config, request, effectiveTimeout);
    if (!response.ok) throw new ApiError(response);
    return response.contentType.includes("application/json") ? (response.data as T) : (undefined as T);
  }

  const controller = new AbortController();
  const timeoutId = effectiveTimeout > 0 ? window.setTimeout(() => controller.abort(), effectiveTimeout) : null;
  const signal = requestInit.signal ? AbortSignal.any([controller.signal, requestInit.signal]) : controller.signal;

  try {
    const response = await fetch(`${config.serverUrl}${path}`, {
      ...requestInit,
      headers: {
        ...headersForConfig(config, Boolean(requestInit.body)),
        ...(requestInit.headers ?? {}),
      },
      signal,
    });

    if (!response.ok) {
      const contentType = response.headers.get("content-type") ?? "";
      const text = await response.text();
      let data: unknown = null;
      if (contentType.includes("application/json") && text) {
        try {
          data = JSON.parse(text);
        } catch {
          data = null;
        }
      }
      throw new ApiError({ status: response.status, data, text });
    }

    if (!response.headers.get("content-type")?.includes("application/json")) {
      return undefined as T;
    }
    return (await response.json()) as T;
  } finally {
    if (timeoutId) window.clearTimeout(timeoutId);
  }
}

export async function desktopRequestWithTimeout(
  desktopApi: NonNullable<NonNullable<Window["ardenDesktop"]>["api"]>,
  config: AppConfig,
  request: Parameters<NonNullable<NonNullable<Window["ardenDesktop"]>["api"]>["request"]>[1],
  timeoutMs: number,
): Promise<ApiBridgeResponse> {
  if (timeoutMs <= 0) return desktopApi.request(config, request);
  let timeoutId: ReturnType<typeof globalThis.setTimeout> | null = null;
  try {
    return await Promise.race([
      desktopApi.request(config, request),
      new Promise<never>((_, reject) => {
        timeoutId = globalThis.setTimeout(() => {
          reject(new Error(`Request timed out for ${request.method ?? "GET"} ${request.path}`));
        }, timeoutMs);
      }),
    ]);
  } finally {
    if (timeoutId) globalThis.clearTimeout(timeoutId);
  }
}

export async function checkHealth(config: AppConfig): Promise<HealthCheck> {
  try {
    const health = await apiWithConfig<{
      auth?: boolean;
      version?: string;
      has_providers?: boolean;
    }>(config, "/health", { timeout: 5000 } as RequestInit & { timeout: number });
    return {
      ok: health.auth !== false,
      version: health.version ?? null,
      hasProviders: health.has_providers ?? true,
    };
  } catch {
    return { ok: false, version: null, hasProviders: true };
  }
}

export async function validateConnection(config: AppConfig): Promise<HealthCheck> {
  const normalized = normalizeConfig(config);
  if (!normalized.apiKey) throw new Error("API key is required");
  const health = await checkHealth(normalized);
  if (!health.ok) {
    throw new Error(health.version ? "Invalid API key" : "Could not reach Arden server");
  }
  return health;
}

function loadBrowserConfig(): AppConfig {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return { ...DEFAULT_CONFIG };
  try {
    return normalizeConfig(JSON.parse(raw));
  } catch {
    return { ...DEFAULT_CONFIG };
  }
}

export async function loadInitialConfig(): Promise<AppConfig> {
  const desktopConfig = window.ardenDesktop?.config;
  if (!desktopConfig) return loadBrowserConfig();
  return normalizeConfig(await desktopConfig.get());
}

export async function saveConfig(config: AppConfig): Promise<AppConfig> {
  const normalized = normalizeConfig(config);
  const desktopConfig = window.ardenDesktop?.config;
  if (desktopConfig) {
    const saved = await desktopConfig.set(normalized);
    localStorage.removeItem(STORAGE_KEY);
    return saved;
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
  return normalized;
}

const COMPACT_TIMEOUT_MS = 600_000;

export interface CompactResponse {
  status: string;
  message?: string;
  message_count?: number;
  before_messages?: number;
  after_messages?: number;
  messages_compressed?: number;
}

export async function compactSessionApi(config: AppConfig, sessionId: string): Promise<CompactResponse> {
  return apiWithConfig<CompactResponse>(config, "/compact", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
    timeout: COMPACT_TIMEOUT_MS,
  } as RequestInit & { timeout: number });
}
