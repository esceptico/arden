/** Copy `text` to the clipboard, returning whether it actually landed.
 *
 *  Order matters. The Electron bridge is authoritative in the packaged app.
 *  `navigator.clipboard.writeText` can *resolve without writing* in this
 *  webview (it doesn't throw), so it can't be trusted as a mid-tier fallback —
 *  relying on it makes a copy button flash "Copied" while the clipboard stays
 *  empty. `document.execCommand("copy")` returns a real success boolean and
 *  works in restrictive webviews, so it's the reliable fallback; the async
 *  Clipboard API is the last resort for plain secure browser contexts. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (await window.ardenDesktop?.clipboard?.writeText(text)) return true;
  } catch {
    // bridge unavailable or rejected — fall through
  }

  if (execCommandCopy(text)) return true;

  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function execCommandCopy(text: string): boolean {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.opacity = "0";
    ta.setAttribute("readonly", "");
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
