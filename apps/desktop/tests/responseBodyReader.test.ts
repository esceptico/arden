import { expect, test } from "bun:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { consumeResponseBody } = require("../electron/response-body-reader.cjs") as {
  consumeResponseBody(
    body: ReadableStream<Uint8Array>,
    signal: AbortSignal,
    onChunk: (chunk: Uint8Array) => void | Promise<void>,
  ): Promise<void>;
};

test("abort cancels and unlocks a pending response body", async () => {
  let cancellations = 0;
  const body = new ReadableStream<Uint8Array>({
    cancel() {
      cancellations += 1;
    },
  });
  const controller = new AbortController();
  const consuming = consumeResponseBody(body, controller.signal, () => {});

  controller.abort();
  await consuming;

  expect(cancellations).toBe(1);
  expect(body.locked).toBe(false);
});

test("normal EOF unlocks without cancelling the response body", async () => {
  let cancellations = 0;
  const chunks: string[] = [];
  const body = new ReadableStream<Uint8Array>({
    start(stream) {
      stream.enqueue(new TextEncoder().encode("event"));
      stream.close();
    },
    cancel() {
      cancellations += 1;
    },
  });

  await consumeResponseBody(body, new AbortController().signal, chunk => {
    chunks.push(new TextDecoder().decode(chunk));
  });

  expect(chunks).toEqual(["event"]);
  expect(cancellations).toBe(0);
  expect(body.locked).toBe(false);
});

test("handler failure cancels and unlocks the response body", async () => {
  let cancellations = 0;
  const body = new ReadableStream<Uint8Array>({
    start(stream) {
      stream.enqueue(new Uint8Array([1]));
    },
    cancel() {
      cancellations += 1;
    },
  });

  await expect(consumeResponseBody(body, new AbortController().signal, () => {
    throw new Error("invalid frame");
  })).rejects.toThrow("invalid frame");

  expect(cancellations).toBe(1);
  expect(body.locked).toBe(false);
});
