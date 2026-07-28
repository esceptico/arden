import { expect, test } from "bun:test";
import { serializeFrontmatter } from "@/features/memory/lib/format";

test("frontmatter serialization preserves ambiguous strings as strings", () => {
  const result = serializeFrontmatter({
    booleanLike: "true",
    nullLike: "null",
    yesLike: "yes",
    dateLike: "2026-07-28",
    numberLike: "42",
    title: "Atlas Prime",
  });

  expect(result).toContain('booleanLike: "true"');
  expect(result).toContain('nullLike: "null"');
  expect(result).toContain('yesLike: "yes"');
  expect(result).toContain('dateLike: "2026-07-28"');
  expect(result).toContain('numberLike: "42"');
  expect(result).toContain('title: "Atlas Prime"');
});
