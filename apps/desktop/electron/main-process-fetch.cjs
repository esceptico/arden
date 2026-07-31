/**
 * Pin Electron main-process HTTP to Chromium's network stack.
 *
 * Do not replace this with Node's global fetch: Electron 43 bundles Node
 * 24.18 / undici 7.28, whose HTTP/1 writer can throw an uncaught
 * `setTypeOfService EINVAL` on macOS. Electron's net.fetch has the same
 * Response, streaming-body, and AbortSignal contracts needed by the API and
 * SSE bridges without going through undici.
 */
function createMainProcessFetch(electronNet) {
  if (!electronNet || typeof electronNet.fetch !== "function") {
    throw new TypeError("Electron net.fetch is unavailable");
  }

  return function mainProcessFetch(input, init) {
    return electronNet.fetch(input, init);
  };
}

module.exports = { createMainProcessFetch };
