import type { MemoryVaultChange, SequencedMemoryVaultChange } from "@/stores/types";

export const MEMORY_VAULT_CHANGE_CAP = 64;

export function appendMemoryVaultChange(
  current: readonly SequencedMemoryVaultChange[],
  change: MemoryVaultChange,
): readonly SequencedMemoryVaultChange[] {
  if (change.seq == null || current.some((candidate) => candidate.seq === change.seq)) return current;
  if (current.length >= MEMORY_VAULT_CHANGE_CAP && change.seq < current[0]!.seq) return current;
  const next = [...current, { ...change, seq: change.seq }].sort((left, right) => left.seq - right.seq);
  return next.length > MEMORY_VAULT_CHANGE_CAP ? next.slice(-MEMORY_VAULT_CHANGE_CAP) : next;
}
