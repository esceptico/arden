import { AlertCircle, ArrowUpRight, Copy, Link2 } from "lucide-react";
import { copyText } from "@/lib/clipboard";
import { resolveWikiTarget } from "@/features/memory/lib/wikiResolution";
import type {
  MemoryArtifactDetail,
  MemoryLink,
  MemoryOperation,
  MemorySourceRef,
  PageEditHistory,
  PageLinks,
} from "@/features/memory/lib/notebookTypes";

function scopeText(scope: { kind: string; key: string | null }) {
  return scope.key ? `${scope.kind}:${scope.key}` : scope.kind;
}

function safeHttpUrl(ref: string) {
  try {
    const url = new URL(ref);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
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

function SourceRow({ source, links, onNavigate }: {
  source: MemorySourceRef;
  links: PageLinks;
  onNavigate: (path: string, anchor: string | null) => void;
}) {
  const url = safeHttpUrl(source.ref);
  const directLink = links.outgoing.find((link) => link.resolvedPath === source.ref && link.status === "resolved");
  const activate = () => {
    if (directLink) {
      const resolved = resolveWikiTarget(links, directLink.target);
      if (resolved) onNavigate(resolved.path, resolved.anchor);
      return;
    }
    void copyText(source.ref);
  };
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
            aria-label={directLink ? `Open source ${source.ref}` : `Copy source ${source.ref}`}
            onClick={activate}
            onKeyDown={(event) => {
              if (event.key !== "Enter" && event.key !== " ") return;
              event.preventDefault();
              activate();
            }}
            className="rounded p-1 text-muted hover:bg-surface-soft hover:text-ink"
          >
            {directLink ? <ArrowUpRight className="size-3.5" /> : <Copy className="size-3.5" />}
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

function LinkRow({ link, kind, onNavigate }: {
  link: MemoryLink;
  kind: "outgoing" | "backlink";
  onNavigate: (path: string, anchor: string | null) => void;
}) {
  const path = kind === "backlink" ? link.sourcePath : link.resolvedPath;
  const anchor = kind === "backlink" ? link.heading : link.target.split("#", 2)[1] ?? null;
  return (
    <li className="rounded-[8px] bg-surface-soft/40 p-2.5 text-xs">
      <button
        type="button"
        disabled={!path}
        aria-label={kind === "backlink" ? `Open backlink from ${link.sourcePath}` : `Open outgoing link ${link.display}`}
        onClick={() => path && onNavigate(path, anchor)}
        className="flex w-full items-center gap-1.5 text-left font-medium text-ink disabled:text-faint"
      >
        <Link2 className="size-3.5 shrink-0" />
        <span className="truncate">{kind === "backlink" ? link.sourcePath : link.display}</span>
        {link.status !== "resolved" && <span className="ml-auto text-faint">{link.status}</span>}
      </button>
      {link.heading && <div className="mt-1 text-faint">{link.heading}</div>}
      <p className="mt-1 leading-relaxed text-muted">{link.context}</p>
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

export function MemoryInspector({ page, links, history, loading, error, onNavigate }: {
  page: MemoryArtifactDetail;
  links: PageLinks | null;
  history: PageEditHistory | null;
  loading: boolean;
  error: string | null;
  onNavigate: (path: string, anchor: string | null) => void;
}) {
  if (loading) return <div role="status" className="p-4 text-xs text-muted">Loading trust context…</div>;
  if (error) return <div role="alert" className="p-4 text-xs text-danger">{error}</div>;
  if (!links || !history) return <div className="p-4 text-xs text-muted">Trust context unavailable.</div>;
  const operations = history.events.flatMap((event) => [...event.operations, ...event.reviewOperations]);
  const sources = operations.flatMap(operationSources);
  const scoped = operations.filter((operation): operation is Extract<MemoryOperation, { scope: unknown }> => "scope" in operation && operation.scope != null);
  const scopeLabels = [...new Set(scoped.map((operation) => scopeText(operation.scope!)))];
  const lifecycle = operations.flatMap((operation) => operationTargets(operation).map((target) => ({ operation, target })));
  const pending = history.events.flatMap((event) => event.questions.map((question) => ({ event, question })));
  return (
    <div className="flex h-full min-h-0 flex-col bg-bg-main">
      <div className="border-b border-line-soft px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">Trust context</h2>
        <p className="text-2xs text-muted">Links, evidence, and history</p>
      </div>
      <div className="flex-1 space-y-6 overflow-y-auto p-4 scroll-thin">
        <Section id="links" title="Links">
          <div className="text-2xs text-faint">Backlinks · {links.totalBacklinks}</div>
          <ul className="grid gap-1.5">{links.backlinks.map((link, index) => <LinkRow key={`back:${index}`} link={link} kind="backlink" onNavigate={onNavigate} />)}</ul>
          <div className="mt-1 text-2xs text-faint">Outgoing · {links.totalOutgoing}</div>
          <ul className="grid gap-1.5">{links.outgoing.map((link, index) => <LinkRow key={`out:${index}`} link={link} kind="outgoing" onNavigate={onNavigate} />)}</ul>
          {links.stale && <div className="text-warning">Link index is refreshing.</div>}
        </Section>
        <Section id="scope" title="Scope">
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-1 text-xs">
            <dt className="text-faint">Page placement</dt><dd className="break-all font-mono text-muted">{page.path}</dd>
            <dt className="text-faint">Page scope</dt><dd className="text-muted">{scopeText(page.scope)}</dd>
            {scopeLabels.map((scope) => <div key={scope} className="contents"><dt className="text-faint">Record scope</dt><dd className="text-muted">{scope}</dd></div>)}
          </dl>
        </Section>
        <Section id="evidence" title="Evidence">
          {sources.length ? <ul className="grid gap-1.5">{sources.map((source, index) => <SourceRow key={`${source.ref}:${index}`} source={source} links={links} onNavigate={onNavigate} />)}</ul> : <p className="text-xs text-faint">No source evidence recorded.</p>}
        </Section>
        <Section id="lifecycle" title="Lifecycle">
          {lifecycle.length ? <ul className="grid gap-1 text-xs text-muted">{lifecycle.map(({ operation, target }) => (
            <li key={`${operation.id}:${target}`} className="rounded-[8px] bg-surface-soft/40 p-2">
              <span className="font-medium text-ink">{operation.kind}</span> · predecessor/target <span className="font-mono">{target}</span>
            </li>
          ))}</ul> : <p className="text-xs text-faint">No lifecycle relationships.</p>}
        </Section>
        <Section id="page-events" title="Page events">
          <ul className="grid gap-1.5">{history.events.map((event) => (
            <li key={event.id} className="rounded-[8px] bg-surface-soft/40 p-2.5 text-xs">
              <div className="flex items-center justify-between gap-2"><strong className="text-ink">{event.actor}</strong><span className="text-faint">{event.occurredAt}</span></div>
              <div className="mt-1 font-mono text-muted">{event.baseRevision} → {event.resultRevision}</div>
              <div className="mt-1 text-faint">{event.eventType.toLowerCase().replaceAll("_", " ")} · {event.origin} · {event.reconciliation.replaceAll("_", " ")}</div>
              <div className="mt-1 text-faint">{event.operations.length} applied · {event.reviewOperations.length} review</div>
            </li>
          ))}</ul>
        </Section>
        <Section id="pending-review" title="Pending review">
          {pending.length ? <ul className="grid gap-1.5">{pending.map(({ event, question }) => <li key={question.id} className="rounded-[8px] bg-warning/10 p-2.5 text-xs text-ink">{question.question}<div className="mt-1 font-mono text-faint">{event.id}</div></li>)}</ul> : <p className="text-xs text-faint">No pending questions.</p>}
        </Section>
      </div>
    </div>
  );
}
