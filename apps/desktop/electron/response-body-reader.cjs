/** Consume one fetch response body and always release its network stream. */
async function consumeResponseBody(body, signal, onChunk) {
  const reader = body.getReader();
  let completed = false;
  let cancellation = null;

  const cancel = () => {
    cancellation ??= reader.cancel();
    return cancellation;
  };
  const onAbort = () => {
    void cancel();
  };

  signal.addEventListener("abort", onAbort, { once: true });
  try {
    if (signal.aborted) await cancel();
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (done) {
        completed = true;
        return;
      }
      await onChunk(value);
    }
  } finally {
    signal.removeEventListener("abort", onAbort);
    try {
      if (!completed) await cancel();
    } finally {
      reader.releaseLock();
    }
  }
}

module.exports = { consumeResponseBody };
