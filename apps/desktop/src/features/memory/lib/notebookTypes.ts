import type { MemoryFrontmatter } from "@/features/memory/components/MemoryProperties";

export interface MemoryItem {
  id: string;
  kind: string;
  updatedAt: string;
  content: string;
  labels: string[];
}

export interface MemoryArtifactSummary {
  pageId: string;
  path: string;
  title: string;
  kind: string;
  source: "wiki";
  revision: string;
  head: string;
  aliases: string[];
  lifecycle: string;
  snippet: string | null;
  summary: string | null;
  editable: boolean;
  updatedAt: string | null;
  createdAt: string | null;
}

export interface MemoryArtifactDetail extends MemoryArtifactSummary {
  content: string;
  editableContent: string;
  frontmatter: MemoryFrontmatter;
}
