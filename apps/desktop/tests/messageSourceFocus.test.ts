import { expect, test } from "bun:test";
import {
  firstMessageIdInSourceFocus,
  messageInSourceFocus,
  type MessageSourceFocus,
} from "@/lib/messageSourceFocus";

const focus: MessageSourceFocus = {
  sessionId: "session-1",
  messageStart: 2,
  messageEnd: 5,
  nonce: 1,
};

test("matches messages inside the focused source range", () => {
  expect(messageInSourceFocus({ sourceIndex: 2 }, focus, "session-1")).toBe(true);
  expect(messageInSourceFocus({ sourceIndex: 4 }, focus, "session-1")).toBe(true);
  expect(messageInSourceFocus({ sourceIndex: 5 }, focus, "session-1")).toBe(false);
  expect(messageInSourceFocus({ sourceIndex: 3 }, focus, "other-session")).toBe(false);
  expect(messageInSourceFocus({}, focus, "session-1")).toBe(false);
});

test("finds the first loaded message inside the focused range", () => {
  const messages = new Map([
    ["a", { sourceIndex: 1 }],
    ["b", { sourceIndex: 3 }],
    ["c", { sourceIndex: 4 }],
  ]);

  expect(firstMessageIdInSourceFocus(["a", "b", "c"], messages, focus, "session-1")).toBe("b");
  expect(firstMessageIdInSourceFocus(["a", "b", "c"], messages, focus, "other-session")).toBeNull();
});
