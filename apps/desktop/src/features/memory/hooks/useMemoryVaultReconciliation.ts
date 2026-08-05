import { useCallback, useEffect, useRef } from "react";
import { useStore } from "@/stores";

interface MemoryVaultReconciliationOptions {
  selectedPath: string | null;
  factsActive: boolean;
  invalidateDetail: (path: string) => void;
  refreshSelectedDetail: () => void;
  refreshSummaries: (onAccepted: () => void) => Promise<boolean>;
  refreshFacts: () => void;
}

export function useMemoryVaultReconciliation({
  selectedPath,
  factsActive,
  invalidateDetail,
  refreshSelectedDetail,
  refreshSummaries,
  refreshFacts,
}: MemoryVaultReconciliationOptions): void {
  const vaultVersion = useStore((state) => state.memoryVaultVersion);
  const vaultChanges = useStore((state) => state.memoryVaultChanges);
  const changesRef = useRef(vaultChanges);
  const selectedPathRef = useRef(selectedPath);
  const factsActiveRef = useRef(factsActive);
  const invalidateDetailRef = useRef(invalidateDetail);
  const refreshSelectedDetailRef = useRef(refreshSelectedDetail);
  const refreshSummariesRef = useRef(refreshSummaries);
  const refreshFactsRef = useRef(refreshFacts);
  // The initial summary load already includes every change retained before
  // Memory mounted. Only reconcile sequences delivered after that baseline.
  const processedSeqs = useRef(new Set(vaultChanges.map((change) => change.seq)));
  const drainRunning = useRef(false);
  const drainRequested = useRef(false);
  const drainRef = useRef<(() => Promise<void>) | null>(null);
  const unsequencedPendingVersion = useRef<number | null>(null);
  const observedVersion = useRef(vaultVersion);
  const observedChanges = useRef(vaultChanges);
  const mounted = useRef(true);

  selectedPathRef.current = selectedPath;
  factsActiveRef.current = factsActive;
  invalidateDetailRef.current = invalidateDetail;
  refreshSelectedDetailRef.current = refreshSelectedDetail;
  refreshSummariesRef.current = refreshSummaries;
  refreshFactsRef.current = refreshFacts;

  const drain = useCallback(async () => {
    if (!mounted.current) return;
    if (drainRunning.current) {
      drainRequested.current = true;
      return;
    }
    drainRunning.current = true;
    try {
      while (mounted.current) {
        const batch = changesRef.current.filter((change) => !processedSeqs.current.has(change.seq));
        const pendingVersion = unsequencedPendingVersion.current;
        const unsequenced = pendingVersion != null;
        if (batch.length === 0 && !unsequenced) break;

        const currentPath = selectedPathRef.current;
        const touchesSelected = currentPath != null
          && (unsequenced || batch.some((change) => change.paths.includes(currentPath)));
        if (touchesSelected) invalidateDetailRef.current(currentPath);

        const accepted = await refreshSummariesRef.current(() => {
          if (!mounted.current) return;
          if (touchesSelected && selectedPathRef.current === currentPath) {
            refreshSelectedDetailRef.current();
          }
          if (factsActiveRef.current) refreshFactsRef.current();
        });
        if (!accepted) break;

        for (const change of batch) processedSeqs.current.add(change.seq);
        if (pendingVersion != null && unsequencedPendingVersion.current === pendingVersion) {
          unsequencedPendingVersion.current = null;
        }
      }
    } finally {
      drainRunning.current = false;
      if (drainRequested.current && mounted.current) {
        drainRequested.current = false;
        queueMicrotask(() => void drainRef.current?.());
      }
    }
  }, []);
  drainRef.current = drain;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    const previousVersion = observedVersion.current;
    const previousSeqs = new Set(observedChanges.current.map((change) => change.seq));
    const addedSequenceCount = vaultChanges.reduce(
      (count, change) => count + (previousSeqs.has(change.seq) ? 0 : 1),
      0,
    );
    changesRef.current = vaultChanges;
    const retained = new Set(vaultChanges.map((change) => change.seq));
    processedSeqs.current = new Set(
      [...processedSeqs.current].filter((sequence) => retained.has(sequence)),
    );
    observedVersion.current = vaultVersion;
    observedChanges.current = vaultChanges;
    if (vaultVersion - previousVersion > addedSequenceCount) {
      // A version without a retained sequence is an intentionally coarse
      // invalidation (or queue overflow), so the selected detail is unknown.
      unsequencedPendingVersion.current = vaultVersion;
    }
    void drain();
  }, [drain, vaultChanges, vaultVersion]);
}
