import { motion } from "motion/react";
import { X } from "@/components/icons";
import { type ImageBlock } from "@/stores";
import { ICON } from "@/lib/icons";
import { RISE_IN, RISE_SETTLED } from "@/lib/tokens/motion";
import { SECTION_ENTER, SECTION_EXIT } from "@/features/chat/lib/composerMotion";

export function ComposerImageStrip({
  images,
  onRemove,
}: {
  images: ImageBlock[];
  onRemove: (index: number) => void;
}) {
  return (
    <motion.div
      key="pending-images"
      initial={RISE_IN}
      animate={RISE_SETTLED}
      exit={SECTION_EXIT}
      transition={SECTION_ENTER}
      className="flex flex-wrap gap-2 px-3 pt-2"
    >
      {images.map((img, i) => (
        <div key={i} className="relative">
          <img
            src={`data:${img.media_type};base64,${img.data}`}
            alt=""
            className="h-14 w-14 rounded-md object-cover outline outline-1 -outline-offset-1 outline-black/10 dark:outline-white/10"
          />
          <button
            type="button"
            onClick={() => onRemove(i)}
            aria-label="Remove image"
            className="absolute -top-1.5 -right-1.5 grid place-items-center w-4 h-4 rounded-full bg-ink text-on-ink shadow-sm hover:opacity-90 transition-[opacity,scale] duration-check ease-out active:scale-[0.97]"
          >
            <X size={ICON.XS} strokeWidth={2.4} />
          </button>
        </div>
      ))}
    </motion.div>
  );
}
