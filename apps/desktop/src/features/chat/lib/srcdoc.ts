const THEME_VARS = [
  "--color-bg", "--color-bg-main", "--color-surface", "--color-surface-soft", "--color-surface-sunken",
  "--color-code-bg", "--color-line", "--color-line-soft", "--color-line-strong",
  "--color-ink", "--color-ink-soft", "--color-on-ink", "--color-muted", "--color-faint", "--color-whisper",
  "--color-accent", "--color-accent-soft", "--color-accent-strong",
  "--color-ok", "--color-ok-soft", "--color-warn", "--color-warn-soft", "--color-bad", "--color-bad-soft",
  "--color-info", "--font-sans", "--font-mono", "--shadow-sm", "--shadow-md", "--shadow-pop",
  "--color-surface-1", "--color-surface-2", "--color-surface-3", "--color-surface-4",
  "--color-surface-5", "--color-surface-6", "--color-surface-7", "--color-surface-8",
  "--shadow-1", "--shadow-2", "--shadow-3", "--shadow-4",
  "--shadow-5", "--shadow-6", "--shadow-7", "--shadow-8",
  // Leaf tokens the shadow chains reference via var(); getComputedStyle does not
  // flatten nested var() on custom properties, so the iframe :root needs these too.
  // Light chains use --shadow-color; dark chains use the --dm-* highlight/ring/drop set.
  "--shadow-color",
  "--dm-hi-base", "--dm-hi-mid", "--dm-hi-high", "--dm-hi-peak",
  "--dm-ring-base", "--dm-ring-mid", "--dm-ring-high", "--dm-drop",
] as const;

export const WIDGET_SANDBOX = "allow-scripts allow-forms"; // NEVER allow-same-origin

// form-action does NOT fall back to default-src — without it, allow-forms
// would let <form action="https://..."> exfiltrate via frame navigation.
export const WIDGET_CSP_META =
  `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; form-action 'none'">`;

export const WIDGET_BRIDGE_SCRIPT = `<script>
(function () {
  "use strict";
  var resolved = false;
  function send(method, params) {
    parent.postMessage({ jsonrpc: "2.0", method: method, params: params }, "*");
  }
  window.arden = {
    submit: function (values) {
      if (resolved) return;
      resolved = true;
      send("ui/submit", { values: values || {} });
    },
    submitForm: function (form) {
      if (!(form instanceof HTMLFormElement)) form = document.querySelector("form");
      if (!form) return;
      var values = {};
      new FormData(form).forEach(function (value, key) {
        if (key in values) {
          if (!Array.isArray(values[key])) values[key] = [values[key]];
          values[key].push(value);
        } else {
          values[key] = value;
        }
      });
      window.arden.submit(values);
    },
    cancel: function () {
      if (resolved) return;
      resolved = true;
      send("ui/cancel", {});
    },
  };
  function reportSize() {
    send("ui/size-changed", { height: document.documentElement.scrollHeight });
  }
  new ResizeObserver(reportSize).observe(document.documentElement);
  window.addEventListener("load", reportSize);
  // A sandboxed frame may always navigate ITSELF — no sandbox flag or CSP
  // directive blocks it. Swallow anchor navigation; the Electron
  // will-frame-navigate guard backstops script-initiated navigation.
  document.addEventListener("click", function (event) {
    var anchor = event.target instanceof Element ? event.target.closest("a[href]") : null;
    if (anchor && anchor.getAttribute("href").charAt(0) !== "#") event.preventDefault();
  }, true);
})();
<\/script>`;

// Base style pack: semantic HTML and a few documented classes come pre-styled
// so the model doesn't re-invent form/table CSS in every widget (and so all
// widgets share one look). Documented contract in the render_html tool
// description: .field .grid-2 .chip .actions .muted, button.primary.
export const WIDGET_BASE_CSS =
  `h1,h2{font-size:16px;font-weight:600;margin:0 0 10px}` +
  `h3,h4{font-size:14px;font-weight:600;margin:0 0 8px}` +
  `p{margin:0 0 10px;line-height:1.5}` +
  `a{color:var(--color-accent)}` +
  `hr{border:0;border-top:1px solid var(--color-line-soft);margin:12px 0}` +
  `code,pre{font-family:var(--font-mono);font-size:12.5px;background:var(--color-code-bg,var(--color-surface-soft));border-radius:6px}` +
  `code{padding:1.5px 5px}pre{padding:10px 12px;overflow:auto}pre code{padding:0;background:none}` +
  `table{width:100%;border-collapse:collapse}` +
  `th{text-align:left;font-size:12.5px;color:var(--color-muted);font-weight:600;border-bottom:1px solid var(--color-line)}` +
  `th,td{padding:8px 10px;vertical-align:top}td{border-bottom:1px solid var(--color-line-soft)}` +
  `input:not([type=checkbox]):not([type=radio]),select,textarea{width:100%;padding:8px 10px;border:1px solid var(--color-line);border-radius:8px;background:var(--color-surface);color:var(--color-ink);font:inherit}` +
  `textarea{resize:vertical;min-height:64px}` +
  `input:focus-visible,select:focus-visible,textarea:focus-visible,button:focus-visible{outline:2px solid var(--color-accent);outline-offset:1px}` +
  `input[type=checkbox],input[type=radio],input[type=range]{accent-color:var(--color-accent)}` +
  `button{padding:7px 14px;border:1px solid var(--color-line);border-radius:8px;background:var(--color-surface);color:var(--color-ink);font:inherit;font-weight:500;cursor:pointer}` +
  `button:hover{background:var(--color-surface-soft)}` +
  `button.primary{background:var(--color-ink);color:var(--color-on-ink,var(--color-bg));border-color:var(--color-ink)}` +
  `label{font-weight:600}` +
  `.field{margin-bottom:12px}.field>label{display:block;margin-bottom:6px}` +
  `.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:8px}` +
  `.chip{display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid var(--color-line-soft);border-radius:8px;background:var(--color-surface-soft);font-weight:400;cursor:pointer}` +
  `.chip:has(:checked){border-color:var(--color-accent);background:var(--color-accent-soft)}` +
  `.actions{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}` +
  `.muted{color:var(--color-muted);font-size:12.5px;font-weight:400}`;

/** Snapshot the host's resolved design tokens for the widget's :root. */
export function snapshotThemeVars(root: Element): string {
  const style = getComputedStyle(root);
  return THEME_VARS
    .map((name) => `${name}:${style.getPropertyValue(name).trim()}`)
    .filter((decl) => !decl.endsWith(":"))
    .join(";");
}

export function buildSrcdoc(html: string, themeVars: string): string {
  return (
    `<!DOCTYPE html><html><head><meta charset="utf-8">` +
    WIDGET_CSP_META +
    `<style>:root{${themeVars}}html{margin:0;padding:0}body{margin:0;padding:14px 16px;background:transparent;color:var(--color-ink);font-family:var(--font-sans);font-size:14px}*,*::before,*::after{box-sizing:border-box}${WIDGET_BASE_CSS}</style>` +
    WIDGET_BRIDGE_SCRIPT +
    `</head><body>${html}</body></html>`
  );
}
