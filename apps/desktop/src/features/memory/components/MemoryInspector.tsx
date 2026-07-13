import { AlertCircle, ArrowUpRight, Copy, Link2 } from "lucide-react";
import { copyText } from "@/lib/clipboard";
import type {
  MemoryArtifactDetail,
  MemoryLink,
  MemoryOperation,
  MemorySourceRef,
  PageEditEvent,
  PageEditHistory,
  PageLinks,
} from "@/features/memory/lib/notebookTypes";

function scopeText(scope: { kind: string; key: string | null }) {
  return scope.key ? `${scope.kind}:${scope.key}` : scope.kind;
}

function safeHttpUrl(ref: string) {
  try {
    const url = new URL(ref);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    if (url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

function operationSources(operation: MemoryOperation): MemorySourceRef[] {
  return "sources" in operation ? operation.sources : [];
}

function operationTargets(operation: MemoryOperation) {
  return "targetIds" in operation ? operation.targetIds : [];
}

function SourceRow({ source }: { source: MemorySourceRef }) {
  const url = safeHttpUrl(source.ref);
  const copy = () => { void copyText(source.ref); };
  const state = source.metadata.evidence_state;
  return (
    <li className="rounded-[8px] bg-surface-soft/45 p-2.5 text-xs">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-medium text-ink">{source.kind}{source.role ? ` · ${source.role}` : ""}</div>
          <div className="break-all font-mono text-faint">{source.ref}</div>
        </div>
        {url ? (
          <a href={url} target="_blank" rel="noopener noreferrer" aria-label={`Open source ${source.ref}`} className="rounded p-1 text-muted hover:bg-surface-soft hover:text-ink">
            <ArrowUpRight className="size-3.5" />
          </a>
        ) : (
          <button
            type="button"
            aria-label={`Copy source ${source.ref}`}
            onClick={copy}
            onKeyDown={(event) => {
              if (event.key !== "Enter" && event.key !== " ") return;
              event.preventDefault();
              copy();
            }}
            className="rounded p-1 text-muted hover:bg-surface-soft hover:text-ink"
          >
            <Copy className="size-3.5" />
          </button>
        )}
      </div>
      <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-0.5 text-faint">
        <dt>Occurred</dt><dd className="break-all text-muted">{source.occurredAt ?? "Unknown"} · {source.timePrecision}</dd>
        <dt>Captured</dt><dd className="break-all text-muted">{source.capturedAt ?? "Unknown"}</dd>
        <dt>Hash</dt><dd className="break-all font-mono text-muted">{source.excerptHash ?? "Unavailable"}</dd>
        {source.scope && <><dt>Source scope</dt><dd className="text-muted">{scopeText(source.scope)}</dd></>}
      </dl>
      {(state === "missing" || state === "changed") && (
        <div className="mt-2 flex items-center gap-1 text-warning"><AlertCircle className="size-3" />Evidence {state}</div>
      )}
    </li>
  );
}

function LinkRow({ link, kind, stale, navigationDisabled, onNavigate }: {
  link: MemoryLink;
  kind: "outgoing" | "backlink";
  stale: boolean;
  navigationDisabled: boolean;
  onNavigate: (path: string, anchor: string | null) => void;
}) {
  const path = kind === "backlink" ? link.sourcePath : link.resolvedPath;
  const anchor = kind === "backlink" ? link.heading : link.target.split("#", 2)[1] ?? null;
  return (
    <li className="rounded-[8px] bg-surface-soft/40 p-2.5 text-xs">
      <button
        type="button"
        disabled={navigationDisabled || stale || !path}
        aria-label={kind === "backlink" ? `Open backlink from ${link.sourcePath}` : `Open outgoing link ${link.display}`}
        onClick={() => !stale && path && onNavigate(path, anchor)}
        className="flex w-full items-center gap-1.5 text-left font-medium text-ink disabled:text-faint"
      >
        <Link2 className="size-3.5 shrink-0" />
        <span className="truncate">{kind === "backlink" ? link.sourcePath : link.display}</span>
        {link.status !== "resolved" && <span className="ml-auto text-faint">{link.status}</span>}
      </button>
      {link.heading && <div className="mt-1 text-faint">{link.heading}</div>}
      <p className="mt-1 leading-relaxed text-muted">{link.context}</p>
      <div className="mt-1 text-2xs text-faint">Line {link.line}:{link.column}</div>
      {link.candidates.length > 1 && <div className="mt-1 break-all font-mono text-2xs text-faint">Candidates: {link.candidates.join(", ")}</div>}
    </li>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section data-memory-inspector-section={id} className="grid gap-2">
      <h3 className="text-2xs font-semibold uppercase tracking-[0.08em] text-faint">{title}</h3>
      {children}
    </section>
  );
}

function ErrorText({ message }: { message: string }) {
  return <p role="alert" className="text-xs text-danger">{message}</p>;
}

function EventRows({ events }: { events: PageEditEvent[] }) {
  return (
    <ul className="grid gap-1.5">{events.map((event) => (
      <li key={event.id} className="rounded-[8px] bg-surface-soft/40 p-2.5 text-xs">
        <div className="flex items-center justify-between gap-2"><strong className="text-ink">{event.actor}</strong><span className="text-faint">{event.occurredAt}</span></div>
        <div className="mt-1 font-mono text-muted">{event.baseRevision} → {event.resultRevision}</div>
        <div className="mt-1 text-faint">{event.eventType.toLowerCase().replaceAll("_", " ")} · {event.origin} · {event.reconciliation.replaceAll("_", " ")}</div>
        <div className="mt-1 text-faint">{event.operations.length} applied · {event.reviewOperations.length} review</div>
      </li>
    ))}</ul>
  );
}

export function MemoryInspector({
  page,
  links,
  history,
  linksLoading,
  historyLoading,
  linkError,
  historyError,
  linksLoadingMore = false,
  historyLoadingMore = false,
  navigationDisabled = false,
  onNavigate,
  onRetryLinks,
  onLoadMoreLinks,
  onLoadMoreHistory,
}: {
  page: MemoryArtifactDetail;
  links: PageLinks | null;
  history: PageEditHistory | null;
  linksLoading: boolean;
  historyLoading: boolean;
  linkError: string | null;
  historyError: string | null;
  linksLoadingMore?: boolean;
  historyLoadingMore?: boolean;
  navigationDisabled?: boolean;
  onNavigate: (path: string, anchor: string | null) => void;
  onRetryLinks?: () => void;
  onLoadMoreLinks?: () => void;
  onLoadMoreHistory?: () => void;
}) {
  const appliedOperations = history?.events.flatMap((event) => event.operations) ?? [];
  const proposedOperations = history?.events.flatMap((event) => event.reviewOperations) ?? [];
  const appliedSources = appliedOperations.flatMap(operationSources);
  const proposedSources = proposedOperations.flatMap(operationSources);
  const appliedScopes = [...new Set(appliedOperations.flatMap((operation) => "scope" in operation && operation.scope ? [scopeText(operation.scope)] : []))];
  const proposedScopes = [...new Set(proposedOperations.flatMap((operation) => "scope" in operation && operation.scope ? [scopeText(operation.scope)] : []))];
  const appliedTargets = appliedOperations.flatMap((operation) => operationTargets(operation).map((target) => ({ operation, target })));
  const proposedTargets = proposedOperations.flatMap((operation) => operationTargets(operation).map((target) => ({ operation, target })));
  const pending = history?.events
    .filter((event) => !history.events.some((resolver) => resolver.reconciliation === "applied"
      && resolver.sequence > event.sequence
      && (resolver.reconcilesEventId === event.id || resolver.reviewEventId === event.id)))
    .flatMap((event) => event.questions.map((question) => ({ event, question }))) ?? [];
  const partialHistory = history != null && history.events.length < history.total;
  const moreLinks = links != null && (links.outgoing.length < links.totalOutgoing || links.backlinks.length < links.totalBacklinks);

  return (
    <div className="flex h-full min-h-0 flex-col bg-bg-main">
      <div className="border-b border-line-soft px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">Trust context</h2>
        <p className="text-2xs text-muted">Links, evidence, and history</p>
      </div>
      <div className="flex-1 space-y-6 overflow-y-auto p-4 scroll-thin">
        <Section id="links" title="Links">
          {linkError && <ErrorText message={linkError} />}
          {linksLoading && !links && <p role="status" className="text-xs text-muted">Loading links…</p>}
          {links && <>
            {links.stale && <div className="flex items-center justify-between gap-3 text-xs text-warning">
              <span>Link index is refreshing. Navigation is temporarily disabled.</span>
              {onRetryLinks && <button type="button" aria-label="Refresh memory links" onClick={onRetryLinks} className="shrink-0 font-medium hover:text-ink">Refresh links</button>}
            </div>}
            <div className="text-2xs text-faint">Backlinks · {links.totalBacklinks}</div>
            <ul className="grid gap-1.5">{links.backlinks.map((link, index) => <LinkRow key={`back:${link.sourcePath}:${link.line}:${index}`} link={link} kind="backlink" stale={links.stale} navigationDisabled={navigationDisabled} onNavigate={onNavigate} />)}</ul>
            <div className="mt-1 text-2xs text-faint">Outgoing · {links.totalOutgoing}</div>
            <ul className="grid gap-1.5">{links.outgoing.map((link, index) => <LinkRow key={`out:${link.target}:${link.line}:${index}`} link={link} kind="outgoing" stale={links.stale} navigationDisabled={navigationDisabled} onNavigate={onNavigate} />)}</ul>
            <p className="text-2xs text-faint">Showing {links.outgoing.length} of {links.totalOutgoing} outgoing · {links.backlinks.length} of {links.totalBacklinks} backlinks</p>
            {moreLinks && onLoadMoreLinks && <button type="button" aria-label="Load more memory links" disabled={linksLoadingMore || links.stale} onClick={onLoadMoreLinks} className="text-xs font-medium text-muted hover:text-ink disabled:opacity-50">{linksLoadingMore ? "Loading…" : "Load more links"}</button>}
          </>}
        </Section>

        <Section id="scope" title="Scope">
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-1 text-xs">
            <dt className="text-faint">Page placement</dt><dd className="break-all font-mono text-muted">{page.path}</dd>
            <dt className="text-faint">Page scope</dt><dd className="text-muted">{scopeText(page.scope)}</dd>
            {appliedScopes.map((scope) => <div key={`applied:${scope}`} className="contents"><dt className="text-faint">Record scope</dt><dd className="text-muted">{scope}</dd></div>)}
            {proposedScopes.map((scope) => <div key={`proposed:${scope}`} className="contents"><dt className="text-faint">Proposed scope</dt><dd className="text-muted">{scope}</dd></div>)}
          </dl>
          {partialHistory && <p className="text-2xs text-faint">Based on {history.events.length} of {history.total} events</p>}
        </Section>

        <Section id="evidence" title="Evidence">
          {historyError && <ErrorText message={historyError} />}
          {historyLoading && !history && <p role="status" className="text-xs text-muted">Loading evidence…</p>}
          {history && <>
            <div className="text-2xs text-faint">Applied evidence</div>
            {appliedSources.length ? <ul className="grid gap-1.5">{appliedSources.map((source, index) => <SourceRow key={`applied:${source.ref}:${index}`} source={source} />)}</ul> : <p className="text-xs text-faint">No applied source evidence.</p>}
            <div className="mt-1 text-2xs text-faint">Proposed evidence</div>
            {proposedSources.length ? <ul className="grid gap-1.5">{proposedSources.map((source, index) => <SourceRow key={`proposed:${source.ref}:${index}`} source={source} />)}</ul> : <p className="text-xs text-faint">No proposed source evidence.</p>}
          </>}
        </Section>

        <Section id="lifecycle" title="Lifecycle">
          {history && (appliedTargets.length || proposedTargets.length) ? <ul className="grid gap-1 text-xs text-muted">
            {appliedTargets.map(({ operation, target }) => <li key={`actual:${operation.id}:${target}`} className="rounded-[8px] bg-surface-soft/40 p-2"><span className="font-medium text-ink">{operation.kind}</span> · Target <span className="font-mono">{target}</span></li>)}
            {proposedTargets.map(({ operation, target }) => <li key={`proposed:${operation.id}:${target}`} className="rounded-[8px] bg-warning/10 p-2"><span className="font-medium text-ink">{operation.kind}</span> · Proposed target <span className="font-mono">{target}</span></li>)}
          </ul> : <p className="text-xs text-faint">No lifecycle relationships.</p>}
        </Section>

        <Section id="page-events" title="Page events">
          {historyError && <ErrorText message={historyError} />}
          {historyLoading && !history && <p role="status" className="text-xs text-muted">Loading page events…</p>}
          {history && <>
            <EventRows events={history.events} />
            <p className="text-2xs text-faint">Showing {history.events.length} of {history.total} events</p>
            {history.nextBeforeSequence != null && onLoadMoreHistory && <button type="button" aria-label="Load older page events" disabled={historyLoadingMore} onClick={onLoadMoreHistory} className="text-xs font-medium text-muted hover:text-ink disabled:opacity-50">{historyLoadingMore ? "Loading…" : "Load older events"}</button>}
          </>}
        </Section>

        <Section id="pending-review" title="Pending review">
          {history && (pending.length ? <ul className="grid gap-1.5">{pending.map(({ event, question }) => <li key={`${event.id}:${question.id}`} className="rounded-[8px] bg-warning/10 p-2.5 text-xs text-ink">{question.question}<div className="mt-1 font-mono text-faint">{event.id}</div></li>)}</ul> : <p className="text-xs text-faint">No pending questions.</p>)}
        </Section>
      </div>
    </div>
  );
}
