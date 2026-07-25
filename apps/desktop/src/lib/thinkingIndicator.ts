import {
  THINKING_ANIMATION_IDS,
  THINKING_INTENSITY_IDS,
  type ThinkingAnimation,
  type ThinkingIntensity,
} from "@/stores/types";

const TREATMENT_COPY: Record<ThinkingAnimation, { label: string; description: string }> = {
  comet: {
    label: "Comet",
    description: "Single arc travels around the rim",
  },
  breath: {
    label: "Breath",
    description: "Wide diffuse halo that breathes slowly",
  },
  border: {
    label: "Border tint",
    description: "Border drifts toward accent — no motion",
  },
  orbit: {
    label: "Send orbit",
    description: "Spinner around the send button only",
  },
};

const INTENSITY_LABELS: Record<ThinkingIntensity, string> = {
  subtle: "Subtle",
  normal: "Normal",
  strong: "Strong",
};

/** Ordered, complete UI choices derived from the persisted ID registries. */
export const THINKING_TREATMENTS = THINKING_ANIMATION_IDS.map((id) => ({
  id,
  ...TREATMENT_COPY[id],
}));

export const THINKING_INTENSITIES = THINKING_INTENSITY_IDS.map((id) => ({
  id,
  label: INTENSITY_LABELS[id],
}));
