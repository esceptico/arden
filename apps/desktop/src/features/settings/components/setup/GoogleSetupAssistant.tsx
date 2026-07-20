import { useState } from "react";
import type { GoogleIntegrationId, GooglePreflightResponse, SetupStatus } from "@/api/settings";
import { connectGoogleServiceApi, getSetupStatusApi, preflightGoogleSetupApi, saveGoogleCredentialsApi } from "@/api/settings";
import { fetchServerConfig, updateServerConfig } from "@/actions/server";
import { useStore } from "@/stores";
import { GOOGLE_SERVICE_OPTIONS, googleChoiceLabel } from "@/features/settings/lib/setupAssistant";
import { SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import { RadioGroup, RadioGroupItem } from "@/components/ui/RadioGroup";

export function GoogleSetupAssistant({ onDone }: { onDone: () => Promise<void> | void }) {
  const config = useStore((s) => s.config);
  const [serviceChoice, setServiceChoice] = useState<GoogleIntegrationId>("gmail");
  const [path, setPath] = useState("");
  const [jsonText, setJsonText] = useState("");
  const [preflight, setPreflight] = useState<GooglePreflightResponse | null>(null);
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  const credentialsValid = Boolean(path.trim()) !== Boolean(jsonText.trim());

  async function saveCredentials() {
    await run("credentials", async () => {
      const payload = path.trim()
        ? { path: path.trim() }
        : { json: JSON.parse(jsonText) as unknown };
      await saveGoogleCredentialsApi(config, payload);
    });
  }

  async function checkPreflight() {
    await run("preflight", async () => {
      setPreflight(await preflightGoogleSetupApi(config, serviceChoice));
    });
  }

  async function connect() {
    await run("connect", async () => {
      await connectGoogleServiceApi(config, serviceChoice);
      await updateServerConfig({ integrations: { [serviceChoice]: true } });
      await fetchServerConfig();
      setStatus(await getSetupStatusApi(config));
      setConnected(true);
    });
  }

  async function verify() {
    await run("verify", async () => {
      setStatus(await getSetupStatusApi(config));
    });
  }

  return (
    <div className="grid gap-4">
      {error && <SettingsInlineError title="Setup assistant error" message={error} />}

      <section className="grid gap-2">
        <div className="text-sm font-medium text-ink">1. Choose service</div>
        <RadioGroup
          aria-label="Google service"
          value={serviceChoice}
          onChange={(v) => setServiceChoice(v as GoogleIntegrationId)}
        >
          {GOOGLE_SERVICE_OPTIONS.map((option, i) => (
            <RadioGroupItem
              key={option.value}
              index={i}
              value={option.value}
              label={option.label}
              description={option.detail}
            />
          ))}
        </RadioGroup>
      </section>

      <section className="grid gap-2">
        <div className="text-sm font-medium text-ink">2. Credentials</div>
        <p className="m-0 text-xs text-muted">Create a Google Cloud OAuth client of type Desktop app, download JSON, then paste it or provide a file path.</p>
        <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/Users/me/Downloads/client_secret.json" disabled={Boolean(jsonText.trim())} />
        <Textarea className="min-h-[100px]" value={jsonText} onChange={(e) => setJsonText(e.target.value)} placeholder='{"installed":{"client_id":"..."}}' disabled={Boolean(path.trim())} />
        <div className="flex justify-end">
          <Button size="md" disabled={!credentialsValid || busy === "credentials"} onClick={() => void saveCredentials()}>
            {busy === "credentials" ? "Saving…" : "Save credentials"}
          </Button>
        </div>
      </section>

      <section className="grid gap-2">
        <div className="text-sm font-medium text-ink">3. Preflight</div>
        <p className="m-0 text-xs text-muted">This repo has no IMAP/app-password Gmail path today, so ntrp uses Google OAuth.</p>
        <Button variant="secondary" size="md" className="justify-self-start" disabled={busy === "preflight"} onClick={() => void checkPreflight()}>
          {busy === "preflight" ? "Checking…" : `Preflight ${googleChoiceLabel(serviceChoice)}`}
        </Button>
        {preflight && (
          <div className="rounded-[10px] border border-line-soft bg-surface-soft/35 px-3 py-2 text-xs text-muted break-all grid gap-1">
            <div>Credentials: {preflight.credentials.path} · {preflight.credentials.valid ? "valid" : "not ready"}</div>
            <div>Scopes: {preflight.scopes.join(", ") || "none"}</div>
            {preflight.warnings.map((warning) => <div key={warning} className="text-warn">{warning}</div>)}
          </div>
        )}
      </section>

      <section className="grid gap-2">
        <div className="text-sm font-medium text-ink">4. Connect</div>
        <p className="m-0 text-xs text-muted">ntrp opens a browser and waits for the local OAuth redirect.</p>
        <Button size="md" className="justify-self-start" disabled={busy === "connect"} onClick={() => void connect()}>
          {busy === "connect" ? "Connecting…" : "Connect Google account"}
        </Button>
      </section>

      <section className="grid gap-2">
        <div className="text-sm font-medium text-ink">5. Verify</div>
        <Button variant="secondary" size="md" className="justify-self-start" disabled={busy === "verify"} onClick={() => void verify()}>
          {busy === "verify" ? "Refreshing…" : "Refresh setup status"}
        </Button>
        {status && (
          <div className="rounded-[10px] border border-line-soft bg-surface-soft/35 px-3 py-2 text-xs text-muted break-all grid gap-1">
            {status.google.provider_statuses.map((provider) => <div key={provider.id}>{provider.label}: {provider.status} ({provider.tool_count} tool{provider.tool_count === 1 ? "" : "s"}){provider.detail ? ` · ${provider.detail}` : ""}</div>)}
            {status.google.google_accounts.map((account) => (
              <div key={account.id}>
                Account: {account.email ?? account.id} · {account.services.join(", ") || "no enabled services"}
              </div>
            ))}
          </div>
        )}
        {connected && (
          <Button size="md" className="justify-self-end" onClick={() => void onDone()}>
            Done
          </Button>
        )}
      </section>
    </div>
  );
}
