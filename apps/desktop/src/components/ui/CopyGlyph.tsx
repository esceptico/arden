import { Copy, Tick02 } from "@/components/icons";
import { IconSwap } from "@/components/ui/IconSwap";

/**
 * The Copy → Check glyph swap shared by every copy button in the app
 * (messages, code blocks, tool output, diagrams). Wrapping the swap in
 * IconSwap keeps both glyphs mounted and transitions only their visual state.
 * Button chrome + clipboard logic stay at each call site —
 * only the icon swap is shared, since those legitimately differ.
 */
export function CopyGlyph({
  copied,
  size,
  checkClassName,
}: {
  copied: boolean;
  size: number;
  /** Applied to the Check only — for sites that tint the success glyph. */
  checkClassName?: string;
}) {
  return (
    <IconSwap
      state={copied ? "b" : "a"}
      iconA={<Copy size={size} />}
      iconB={<Tick02 size={size} className={checkClassName} />}
    />
  );
}
