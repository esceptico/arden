import { motion } from "motion/react";
import { X } from "@/components/icons";
import { Button } from "@/components/ui/Button";
import { RISE_IN, RISE_SETTLED } from "@/lib/tokens/motion";
import { SECTION_ENTER, SECTION_EXIT } from "@/features/chat/lib/composerMotion";

export function ComposerEditingBanner({ onCancel }: { onCancel: () => void }) {
  return (
    <motion.div
      key="editing-banner"
      initial={RISE_IN}
      animate={RISE_SETTLED}
      exit={SECTION_EXIT}
      transition={SECTION_ENTER}
      className="board-composer__editing flex items-center gap-2 px-3 py-1.5 text-xs text-accent-strong bg-accent-soft/40"
    >
      <span>Editing previous message — pressing send will replace it.</span>
      <Button
        variant="ghost"
        size="sm"
        leadingIcon={X}
        onClick={onCancel}
        className="ml-auto"
        title="Cancel edit"
      >
        cancel
      </Button>
    </motion.div>
  );
}
