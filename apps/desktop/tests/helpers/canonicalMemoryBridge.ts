type BridgeResponse = {
  ok: boolean;
  status: number;
  statusText: string;
  contentType: string;
  data: unknown;
  text: string;
};

type BridgeRequest = {
  path: string;
  method?: string;
  body?: string;
};

export interface WikiPageFixture {
  pageId: string;
  path: string;
  title: string;
  content: string;
  version?: string;
  repositoryHead?: string;
  resourceState?: "active" | "archived";
  lifecycle?: string;
  aliases?: string[];
  redirectTo?: string | null;
  metadata?: Record<string, unknown>;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface FactFixture {
  factId: string;
  text: string;
  kind?: string;
  labels?: string[];
  subjects?: string[];
  scope?: { kind: string; key: string | null };
  lifecycle?: string;
  status?: string;
  certainty?: string;
  evidenceClass?: string;
  sources?: Array<{ kind: string; ref: string; capturedAt?: string | null }>;
  createdAt?: string;
  reviewedAt?: string | null;
  reviewAt?: string | null;
  expiresAt?: string | null;
  version?: string;
}

export interface LinkFixture {
  sourcePageId: string;
  target: string;
  alias?: string | null;
  heading?: string | null;
  status?: "resolved" | "ambiguous" | "unresolved";
  targetPageId?: string | null;
  candidates?: string[];
}

export interface HistoryCommitFixture {
  commitId: string;
  parentId?: string | null;
  actor?: string;
  origin?: string;
  reason?: string;
  timestamp?: string;
  changes?: Array<{
    action: string;
    before: ResourceFixture | null;
    after: ResourceFixture | null;
  }>;
  diff?: string;
}

interface ResourceFixture {
  resourceId: string;
  path: string;
  state: "active" | "archived";
  versionId: string;
}

interface BridgeState {
  pages: Map<string, WikiPageFixture>;
  facts: Map<string, FactFixture>;
}

export interface CanonicalMemoryBridgeOptions {
  pages?: WikiPageFixture[];
  facts?: FactFixture[];
  links?: Record<string, { outgoing?: LinkFixture[]; backlinks?: LinkFixture[] }>;
  history?: Record<string, HistoryCommitFixture[]>;
  onRequest?: (
    request: { path: string; method: string; body: unknown },
    state: BridgeState,
  ) => BridgeResponse | undefined | Promise<BridgeResponse | undefined>;
}

export function ok(data: unknown, status = 200): BridgeResponse {
  return {
    ok: true,
    status,
    statusText: status === 201 ? "Created" : "OK",
    contentType: "application/json",
    data,
    text: "",
  };
}

export function error(status: number, detail: unknown): BridgeResponse {
  return {
    ok: false,
    status,
    statusText: "Error",
    contentType: "application/json",
    data: { detail },
    text: "",
  };
}

function rawPage(page: WikiPageFixture, includeContent: boolean) {
  return {
    page_id: page.pageId,
    path: page.path,
    resource_state: page.resourceState ?? "active",
    title: page.title,
    aliases: page.aliases ?? [],
    lifecycle: page.lifecycle ?? "active",
    redirect_to: page.redirectTo ?? null,
    metadata: page.metadata ?? {},
    version: page.version ?? `${page.pageId}:v1`,
    repository_head: page.repositoryHead ?? "wiki-head-1",
    created_at: page.createdAt ?? "2026-07-13T08:00:00Z",
    updated_at: page.updatedAt ?? "2026-07-13T08:00:00Z",
    ...(includeContent ? { content: page.content } : {}),
  };
}

function rawFact(fact: FactFixture) {
  return {
    fact_id: fact.factId,
    text: fact.text,
    kind: fact.kind ?? "fact",
    labels: fact.labels ?? [],
    subjects: fact.subjects ?? ["Me"],
    scope: fact.scope ?? { kind: "user", key: null },
    lifecycle: fact.lifecycle ?? "durable",
    status: fact.status ?? "active",
    certainty: fact.certainty ?? "confirmed",
    evidence_class: fact.evidenceClass ?? "direct",
    sources: (fact.sources ?? []).map((source) => ({
      kind: source.kind,
      ref: source.ref,
      captured_at: source.capturedAt ?? null,
    })),
    created_at: fact.createdAt ?? "2026-07-13T08:00:00Z",
    reviewed_at: fact.reviewedAt ?? null,
    review_at: fact.reviewAt ?? null,
    expires_at: fact.expiresAt ?? null,
    version: fact.version ?? `${fact.factId}:v1`,
  };
}

function rawLink(link: LinkFixture) {
  return {
    source_page_id: link.sourcePageId,
    node: {
      target: link.target,
      alias: link.alias ?? null,
      heading: link.heading ?? null,
    },
    status: link.status ?? "resolved",
    target_page_id: link.targetPageId ?? null,
    candidates: link.candidates ?? [],
  };
}

function rawResource(resource: ResourceFixture | null) {
  return resource == null ? null : {
    resource_id: resource.resourceId,
    path: resource.path,
    state: resource.state,
    version_id: resource.versionId,
  };
}

function requestPageId(path: string): string | null {
  const match = /^\/admin\/wiki\/pages\/([^/?]+)(?:\/(?:links|history|diff|archive))?(?:\?.*)?$/.exec(path);
  return match ? decodeURIComponent(match[1]!) : null;
}

export function installCanonicalMemoryBridge(options: CanonicalMemoryBridgeOptions = {}) {
  const state: BridgeState = {
    pages: new Map((options.pages ?? []).map((page) => [page.pageId, { ...page }])),
    facts: new Map((options.facts ?? []).map((fact) => [fact.factId, { ...fact }])),
  };
  const requests: Array<{ path: string; method: string; body: unknown }> = [];

  window.ardenDesktop = {
    api: {
      request: async (_config, request: BridgeRequest) => {
        const method = request.method ?? "GET";
        const body = request.body ? JSON.parse(request.body) : null;
        const captured = { path: request.path, method, body };
        requests.push(captured);

        const custom = await options.onRequest?.(captured, state);
        if (custom) return custom;

        if (request.path === "/admin/wiki/rename-approvals") return ok({ approvals: [] });
        if (request.path === "/admin/wiki/maintenance-reviews") return ok({ reviews: [] });
        if (request.path.startsWith("/admin/facts?")) {
          return ok({
            facts: [...state.facts.values()].map(rawFact),
            has_more: false,
            next_after: null,
          });
        }
        if (request.path.startsWith("/admin/facts/")) {
          const factId = decodeURIComponent(request.path.slice("/admin/facts/".length));
          const fact = state.facts.get(factId);
          return fact ? ok({ fact: rawFact(fact) }) : error(404, "fact not found");
        }
        if (request.path === "/admin/wiki/pages") {
          return ok({ repository_head: "wiki-head-1", pages: [...state.pages.values()].map((page) => rawPage(page, false)) });
        }

        const pageId = requestPageId(request.path);
        const page = pageId == null ? null : state.pages.get(pageId);
        if (page == null) throw new Error(`Unexpected request: ${method} ${request.path}`);

        if (request.path.includes("/links")) {
          const report = options.links?.[pageId] ?? {};
          return ok({
            page_id: pageId,
            repository_head: page.repositoryHead ?? "wiki-head-1",
            outgoing: (report.outgoing ?? []).map(rawLink),
            backlinks: (report.backlinks ?? []).map(rawLink),
          });
        }
        if (request.path.includes("/history")) {
          const commits = options.history?.[pageId] ?? [];
          return ok({
            page_id: pageId,
            repository_head: page.repositoryHead ?? "wiki-head-1",
            commits: commits.map((commit) => ({
              commit_id: commit.commitId,
              parent_id: commit.parentId ?? null,
              actor: commit.actor ?? "user:desktop",
              origin: commit.origin ?? "desktop",
              reason: commit.reason ?? "edit page",
              timestamp: commit.timestamp ?? "2026-07-13T08:00:00Z",
              changes: (commit.changes ?? []).map((change) => ({
                action: change.action,
                before: rawResource(change.before),
                after: rawResource(change.after),
              })),
            })),
          });
        }
        if (request.path.includes("/diff")) {
          const target = new URL(request.path, "http://arden.test").searchParams.get("target");
          const commit = (options.history?.[pageId] ?? []).find((item) => item.commitId === target);
          const diff = commit?.diff ?? "";
          return ok({
            diff: {
              offset: 0,
              end_offset: diff.length,
              has_more: false,
              unified_diff: diff,
            },
          });
        }
        if (request.path.endsWith("/archive") && method === "POST") {
          state.pages.delete(pageId);
          return ok({
            page_id: pageId,
            path: page.path,
            resource_state: "archived",
            version: page.version ?? `${pageId}:v1`,
            repository_head: "wiki-head-2",
          });
        }
        if (method === "PUT") {
          const input = body as { content: string };
          const next = {
            ...page,
            content: input.content,
            version: `${pageId}:v2`,
            repositoryHead: "wiki-head-2",
            updatedAt: "2026-07-13T08:01:00Z",
          };
          state.pages.set(pageId, next);
          return ok(rawPage(next, true));
        }
        return ok(rawPage(page, true));
      },
    },
  } as Window["ardenDesktop"];

  return {
    requests,
    state,
    updatePage(page: WikiPageFixture) {
      state.pages.set(page.pageId, { ...page });
    },
  };
}
