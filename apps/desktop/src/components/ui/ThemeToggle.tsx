import { useEffect, useState } from "react";
import { useStore } from "@/stores";
import { Moon02, Sun } from "@/components/icons";
import { IconButton } from "@/components/ui/IconButton";
import { IconSwap } from "@/components/ui/IconSwap";
import { Tooltip } from "@/components/ui/Tooltip";
import { ICON } from "@/lib/icons";

/** Resolved light/dark, tracking the OS when the pref is "system". */
function useIsDark(): boolean {
  const theme = useStore((s) => s.prefs.theme);
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia("(prefers-color-scheme: dark)").matches,
  );
  useEffect(() => {
    if (theme !== "system") return;
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setSystemDark(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [theme]);
  return theme === "dark" || (theme === "system" && systemDark);
}

/** One 28px icon button; the shared IconSwap owns the exact glyph bridge. */
export function ThemeToggle() {
  const setPref = useStore((s) => s.setPref);
  const isDark = useIsDark();
  const label = isDark ? "Use light appearance" : "Use dark appearance";

  return (
    <Tooltip label={label}>
      <IconButton
        size="md"
        shape="circle"
        aria-label={label}
        onClick={() => setPref("theme", isDark ? "light" : "dark")}
        className="workspace-rail__theme-toggle shrink-0"
      >
        <IconSwap
          state={isDark ? "b" : "a"}
          iconA={<Sun size={ICON.MD} />}
          iconB={<Moon02 size={ICON.MD} />}
        />
      </IconButton>
    </Tooltip>
  );
}
