import { expect, test } from "bun:test";
import { orderedTraceEntries } from "@/features/chat/components/ActivityTrace";
import type { ActivityItem } from "@/stores/index";

function toolRow(id: string): ActivityItem {
  return { id, kind: "file_read", target: `read_file(${id})`, status: "executed" };
}

test("html widget items lift out of the rows into their own entry", () => {
  const widget: ActivityItem = {
    id: "t2",
    kind: "render_html",
    semanticKind: "html_widget",
    target: "render_html",
    htmlWidget: { html: "<div>x</div>", title: "Chart", mode: "display" },
    status: "executed",
  };
  const entries = orderedTraceEntries([toolRow("t1"), widget, toolRow("t3")], [], null);

  expect(entries.map((e) => e.kind)).toEqual(["rows", "html_widget", "rows"]);
  expect(entries[1].kind === "html_widget" && entries[1].item.id).toBe("t2");
  expect(entries[0].kind === "rows" && entries[0].items.map((it) => it.id)).toEqual(["t1"]);
  expect(entries[2].kind === "rows" && entries[2].items.map((it) => it.id)).toEqual(["t3"]);
});

test("an html_widget call without widget data stays an ordinary row", () => {
  const bare: ActivityItem = {
    id: "t2",
    kind: "render_html",
    semanticKind: "html_widget",
    target: "render_html",
    status: "ongoing",
  };
  const entries = orderedTraceEntries([toolRow("t1"), bare, toolRow("t3")], [], null);

  expect(entries.map((e) => e.kind)).toEqual(["rows"]);
  expect(entries[0].kind === "rows" && entries[0].items.map((it) => it.id)).toEqual([
    "t1",
    "t2",
    "t3",
  ]);
});
