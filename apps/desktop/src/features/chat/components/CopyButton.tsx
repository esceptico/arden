import clsx from "clsx";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { CopyGlyph } from "@/components/ui/CopyGlyph";
import { useTimeoutFlag } from "@/lib/hooks";
import { copyText } from "@/lib/clipboard";
import { ICON } from "@/lib/icons";

export function CopyButton({ getValue }: { getValue: () => string }) {
  const [copied, flashCopied] = useTimeoutFlag(1200);
  const onCopy = async () => {
    if (await copyText(getValue())) {
      flashCopied();
    }
  };
  return (
    <button
      type="button"
      onClick={() => void onCopy()}
      aria-label={copied ? "Copied" : "Copy"}
      className={clsx(
        "ml-auto inline-flex items-center gap-1 h-6 px-1.5 rounded-[var(--r-control)] text-xs font-medium tracking-[var(--tracking-tight)] transition-[background-color,color,scale] duration-check ease-out active:scale-[0.97]",
        copied ? "text-accent-strong bg-accent-soft" : "text-muted hover:bg-surface-soft hover:text-ink",
      )}
    >
      <CopyGlyph copied={copied} size={ICON.XS} />
      <BlurSwap swapKey={copied ? "copied" : "copy"} blur={2}>
        {copied ? "Copied" : "Copy"}
      </BlurSwap>
    </button>
  );
}
