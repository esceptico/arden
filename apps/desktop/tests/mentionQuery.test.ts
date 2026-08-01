import { describe, expect, test } from "bun:test";
import { mentionQueryAt } from "../src/features/chat/lib/composerHelpers";

describe("mentionQueryAt", () => {
  test("leading slash composes from message start", () => {
    expect(mentionQueryAt("/rev", 4)).toEqual({ query: "rev", start: 0 });
    expect(mentionQueryAt("/", 1)).toEqual({ query: "", start: 0 });
  });

  test("mid-text mention after whitespace", () => {
    expect(mentionQueryAt("please /rev", 11)).toEqual({ query: "rev", start: 7 });
    expect(mentionQueryAt("line one\n/sk", 12)).toEqual({ query: "sk", start: 9 });
  });

  test("closes once the name is finished with a space", () => {
    expect(mentionQueryAt("/review now", 11)).toBeNull();
    expect(mentionQueryAt("use /review then", 16)).toBeNull();
  });

  test("never fires mid-word or mid-path", () => {
    expect(mentionQueryAt("foo/bar", 7)).toBeNull();
    expect(mentionQueryAt("see /Users/me", 13)).toBeNull();
    expect(mentionQueryAt("http://x", 8)).toBeNull();
  });

  test("caret position bounds the token", () => {
    // Caret inside the token: query is only what's left of the caret.
    expect(mentionQueryAt("use /rev after", 8)).toEqual({ query: "rev", start: 4 });
    // Caret before the slash: no mention at this caret.
    expect(mentionQueryAt("use /rev", 3)).toBeNull();
  });
});
