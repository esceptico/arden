import type { ReactNode } from "react";
import { Badge, type BadgeTone } from "@/components/ui/Badge";

export function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: BadgeTone }) {
  return (
    <Badge tone={tone} size="md" shape="rounded" variant="outline">
      {children}
    </Badge>
  );
}
