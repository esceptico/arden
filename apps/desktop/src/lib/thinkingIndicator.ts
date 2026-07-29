import {
  THINKING_INTENSITY_IDS,
  type ThinkingIntensity,
} from "@/stores/types";

const INTENSITY_LABELS: Record<ThinkingIntensity, string> = {
  subtle: "Subtle",
  normal: "Normal",
  strong: "Strong",
};

/** Ordered, complete UI choices derived from the persisted ID registry. */
export const THINKING_INTENSITIES = THINKING_INTENSITY_IDS.map((id) => ({
  id,
  label: INTENSITY_LABELS[id],
}));
