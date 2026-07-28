import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, type AppConfig } from "@/api/core";
import {
  listWikiMaintenanceReviews,
  resolveWikiMaintenanceReview,
  type WikiMaintenanceReview,
} from "@/api/wikiMaintenance";
import { useStore } from "@/stores";

export type WikiMaintenanceDecision =
  | { action: "accept" | "reject" }
  | { action: "resolve-manually"; note: string };

export type WikiMaintenanceReviewVerification = "loading" | "ready" | "error";

export function useWikiMaintenanceReviews(config: AppConfig) {
  const reviewSignal = useStore((state) => {
    for (let index = state.memoryVaultChanges.length - 1; index >= 0; index -= 1) {
      const change = state.memoryVaultChanges[index];
      if (change?.reviewRequired) return change.seq;
    }
    return null;
  });
  const [reviews, setReviews] = useState<WikiMaintenanceReview[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verification, setVerification] =
    useState<WikiMaintenanceReviewVerification>("loading");
  const [reconciliationRequired, setReconciliationRequired] = useState(false);
  const reviewsRef = useRef<WikiMaintenanceReview[]>([]);
  const mounted = useRef(true);
  const requestId = useRef(0);
  const mutationPending = useRef(false);
  const refreshRequested = useRef(false);
  const observedReviewSignal = useRef(reviewSignal);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestId.current += 1;
    };
  }, []);

  const refresh = useCallback(async (requireFresh = false) => {
    if (mutationPending.current) {
      refreshRequested.current = true;
      return;
    }
    if (requireFresh) {
      setVerification("loading");
      setError(null);
    }
    const currentRequest = ++requestId.current;
    try {
      const next = await listWikiMaintenanceReviews(config);
      if (!mounted.current || requestId.current !== currentRequest) return;
      reviewsRef.current = next;
      setReviews(next);
      setVerification("ready");
      setReconciliationRequired(false);
      setError(null);
    } catch (reason) {
      if (!mounted.current || requestId.current !== currentRequest) return;
      setVerification("error");
      if (reviewsRef.current.length > 0) setReconciliationRequired(true);
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [config]);

  useEffect(() => {
    void refresh(true);
  }, [refresh]);

  useEffect(() => {
    const onFocus = () => void refresh(reviewsRef.current.length > 0);
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refresh]);

  useEffect(() => {
    if (reviewSignal == null || reviewSignal === observedReviewSignal.current) return;
    observedReviewSignal.current = reviewSignal;
    void refresh(true);
  }, [refresh, reviewSignal]);

  const reconcile = useCallback(async (
    item: WikiMaintenanceReview,
    failure: unknown,
  ) => {
    const currentRequest = ++requestId.current;
    try {
      const next = await listWikiMaintenanceReviews(config);
      if (!mounted.current || requestId.current !== currentRequest) return;
      reviewsRef.current = next;
      setReviews(next);
      setVerification("ready");
      setReconciliationRequired(false);
      if (!next.some((candidate) => candidate.reviewId === item.reviewId)) {
        setError(null);
        return;
      }
      setError(
        failure instanceof ApiError && failure.status === 409
          ? "This question changed. Review the latest version and answer again."
          : `Could not save this answer: ${failure instanceof Error ? failure.message : String(failure)}`,
      );
    } catch (reason) {
      if (!mounted.current || requestId.current !== currentRequest) return;
      setVerification("error");
      setReconciliationRequired(true);
      setError(
        `Answer outcome is unknown. Check its durable status before answering again: ${
          reason instanceof Error ? reason.message : String(reason)
        }`,
      );
    }
  }, [config]);

  const resolve = useCallback(async (
    item: WikiMaintenanceReview,
    decision: WikiMaintenanceDecision,
  ) => {
    if (mutationPending.current || reconciliationRequired || verification !== "ready") return;
    mutationPending.current = true;
    setPending(true);
    setError(null);
    try {
      await resolveWikiMaintenanceReview(config, item, decision);
      if (!mounted.current) return;
      requestId.current += 1;
      setReviews((current) => {
        const next = current.filter((candidate) => candidate.reviewId !== item.reviewId);
        reviewsRef.current = next;
        return next;
      });
      refreshRequested.current = true;
    } catch (reason) {
      if (mounted.current) await reconcile(item, reason);
    } finally {
      mutationPending.current = false;
      if (mounted.current) {
        setPending(false);
        if (refreshRequested.current) {
          refreshRequested.current = false;
          void refresh(true);
        }
      }
    }
  }, [config, reconcile, reconciliationRequired, refresh, verification]);

  return {
    reviews,
    activeReview: reviews[0] ?? null,
    pending,
    error,
    verification,
    reconciliationRequired,
    refresh,
    resolve,
  };
}
