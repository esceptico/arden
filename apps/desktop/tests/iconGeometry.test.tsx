import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { Search } from "@/components/icons";
import { Button } from "@/components/ui/Button";
import { SearchInput } from "@/components/ui/SearchInput";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("shared controls preserve the source icon geometry", () => {
  const raw = renderToStaticMarkup(<Search size={16} />);
  const button = renderToStaticMarkup(<Button leadingIcon={Search}>Search</Button>);
  const search = renderToStaticMarkup(
    <SearchInput value="" onChange={() => {}} placeholder="Search" />,
  );

  for (const markup of [raw, button, search]) {
    expect(markup).toContain('stroke-width="1.5"');
    expect(markup).not.toContain('stroke-width="2"');
  }

  expect(read("../src/components/icons.tsx")).not.toContain("strokeWidth: 1.5");
  expect(read("../src/app/main.tsx")).not.toContain("strokeWidth");
  expect(read("../src/components/ui/Button.tsx")).not.toContain("strokeWidth");
  expect(read("../src/components/ui/SearchInput.tsx")).not.toContain("strokeWidth");
  expect(read("../src/components/ui/SidebarToggle.tsx")).not.toContain("strokeWidth");
  expect(read("../src/features/memory/components/MemoryProperties.tsx")).not.toContain("×");
});
