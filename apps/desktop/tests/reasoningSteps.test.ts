import { expect, test } from "bun:test";
import { parseReasoningSteps } from "@/features/chat/lib/reasoningSteps";

test("OpenAI summary shape splits into labeled steps", () => {
  const content = "**Plan the fix**\n\nWeigh the two options.\n\n**Ship it**\n\nDone deliberating.";
  expect(parseReasoningSteps(content)).toEqual([
    { label: "Plan the fix", body: "Weigh the two options." },
    { label: "Ship it", body: "Done deliberating." },
  ]);
});

test("plain chain-of-thought prose stays one unlabeled step", () => {
  const content = "The user wants X, so I should check Y first.";
  expect(parseReasoningSteps(content)).toEqual([{ label: null, body: content }]);
});

test("bold used mid-sentence never splits", () => {
  const content = "I think **this** matters more than that.";
  expect(parseReasoningSteps(content)).toEqual([{ label: null, body: content }]);
});

test("a label-only summary renders without a body", () => {
  expect(parseReasoningSteps("**Advising reapplication with preparation**")).toEqual([
    { label: "Advising reapplication with preparation", body: "" },
  ]);
});

test("an unterminated trailing label is the next step mid-stream", () => {
  const content = "**Plan the fix**\n\nWeigh the options.\n\n**Ship i";
  expect(parseReasoningSteps(content)).toEqual([
    { label: "Plan the fix", body: "Weigh the options." },
    { label: "Ship i", body: "" },
  ]);
});

test("prose before the first label keeps its own step", () => {
  const content = "Reading the request.\n\n**Plan the fix**\n\nWeigh.";
  expect(parseReasoningSteps(content)).toEqual([
    { label: null, body: "Reading the request." },
    { label: "Plan the fix", body: "Weigh." },
  ]);
});

test("empty content parses to no steps", () => {
  expect(parseReasoningSteps("")).toEqual([]);
});

test("the model's empty-body placeholder is not a body", () => {
  // OpenAI emits `**Title**\n\n<!-- -->` for a part it has a heading but no
  // body for. Markdown renders the comment as nothing, so keeping it left a
  // step that measured as "has a body" while showing none — which also
  // defeated the bodiless timeline's quiet styling.
  expect(parseReasoningSteps("**Weighing the options**\n\n<!-- -->")).toEqual([
    { label: "Weighing the options", body: "" },
  ]);
});

test("a real multi-part summary splits into one step per part", () => {
  // The exact shape the server now produces: parts joined with a blank line.
  // Glued together (the old `"".join`) this collapsed to a single step whose
  // body was a bold blob — the reported "one phrase per reasoning".
  const content = [
    "**Recommending applying next cycle**\n\nThey should reapply in spring.",
    "**Planning timed practice**\n\nTimed sets beat untimed review.",
  ].join("\n\n");

  expect(parseReasoningSteps(content)).toEqual([
    { label: "Recommending applying next cycle", body: "They should reapply in spring." },
    { label: "Planning timed practice", body: "Timed sets beat untimed review." },
  ]);
});

test("glued parts are recovered into separate steps", () => {
  // A server that never separates its summary parts sends them run together.
  // Each title still ends its line, so the break is recoverable.
  const glued = "**Advising reapplication**" + "**Weighing timing**";
  expect(parseReasoningSteps(glued)).toEqual([
    { label: "Advising reapplication", body: "" },
    { label: "Weighing timing", body: "" },
  ]);
});

test("a glued title with a body is recovered too", () => {
  const glued = "**First**\n\nBody one ends here.**Second**\n\nBody two.";
  expect(parseReasoningSteps(glued)).toEqual([
    { label: "First", body: "Body one ends here." },
    { label: "Second", body: "Body two." },
  ]);
});
