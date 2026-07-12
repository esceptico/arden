import type {
  applyPageEdit,
  ArtifactScope,
  getPageHistory,
  getPageLinks,
  MemoryArtifactDetail,
  MemoryArtifactSummary,
  previewPageEdit,
} from "@/api/memoryArtifacts";

export type { ArtifactScope, MemoryArtifactDetail, MemoryArtifactSummary };

export type PageEditPreview = Awaited<ReturnType<typeof previewPageEdit>>;
export type PageEditEvent = Awaited<ReturnType<typeof applyPageEdit>>["event"];
export type PageEditHistory = Awaited<ReturnType<typeof getPageHistory>>;
export type PageLinks = Awaited<ReturnType<typeof getPageLinks>>;
export type MemoryOperation = PageEditPreview["operations"][number];
export type MemoryLink = PageLinks["outgoing"][number];
export type PageEditQuestion = PageEditPreview["questions"][number];
export type PageEditAnalysis = NonNullable<PageEditEvent["analysis"]>;
export type MemorySourceRef = Extract<MemoryOperation, { sources: unknown }>["sources"][number];
export type MemoryScope = NonNullable<Extract<MemoryOperation, { kind: "ADD" }>["scope"]>;
export type MemoryTimePrecision = MemorySourceRef["timePrecision"];
export type NotebookMemoryKind = NonNullable<Extract<MemoryOperation, { kind: "ADD" }>["memoryKind"]>;

export type PageEditDecisionChoice = "note_only" | "forget_memory";

export interface PageEditDecision {
  choice: PageEditDecisionChoice;
  targetIds: string[];
}
