import { useCallback, useEffect, useRef, useState } from "react";
import type { AppConfig } from "@/api/core";
import {
  acceptWikiRenameApproval,
  listWikiRenameApprovals,
  rejectWikiRenameApproval,
  requestWikiRenameApproval,
  type WikiRenameApproval,
  type WikiRenameApprovalResult,
} from "@/api/wiki";

export interface WikiRenameDraft {
  pageId: string;
  oldPath: string;
  oldTitle: string;
}

export function useWikiRenameApprovals(config: AppConfig, onResolved: (result: WikiRenameApprovalResult) => void) {
  const [draft, setDraft] = useState<WikiRenameDraft | null>(null);
  const [approvals, setApprovals] = useState<WikiRenameApproval[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reconciliationRequired, setReconciliationRequired] = useState(false);
  const [verification, setVerification] = useState<"loading" | "ready" | "error">("loading");
  const mounted = useRef(true);
  const requestId = useRef(0);
  const reconciliationRequiredRef = useRef(false);
  const requestFailure = useRef<string | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestId.current += 1;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (reconciliationRequiredRef.current) return;
    setVerification("loading");
    const currentRequest = ++requestId.current;
    try {
      const next = await listWikiRenameApprovals(config);
      if (!mounted.current || requestId.current !== currentRequest) return;
      setApprovals(next);
      if (next.length > 0) setDraft(null);
      setError(null);
      setVerification("ready");
    } catch (reason) {
      if (!mounted.current || requestId.current !== currentRequest) return;
      requestFailure.current = null;
      reconciliationRequiredRef.current = true;
      setReconciliationRequired(true);
      setVerification("error");
      const detail = reason instanceof Error ? reason.message : String(reason);
      setError(`Couldn’t check pending rename approvals: ${detail}`);
    }
  }, [config]);

  const reconcileFailedRequest = useCallback(
    async (failure: string) => {
      requestFailure.current = failure;
      reconciliationRequiredRef.current = true;
      setReconciliationRequired(true);
      const currentRequest = ++requestId.current;
      try {
        const next = await listWikiRenameApprovals(config);
        if (!mounted.current || requestId.current !== currentRequest) return;
        setApprovals(next);
        if (next.length > 0) {
          setDraft(null);
          requestFailure.current = null;
          setError(null);
        } else {
          setError(failure);
        }
        reconciliationRequiredRef.current = false;
        setReconciliationRequired(false);
        setVerification("ready");
      } catch (reason) {
        if (!mounted.current || requestId.current !== currentRequest) return;
        const detail = reason instanceof Error ? reason.message : String(reason);
        setVerification("error");
        setError(`Rename request outcome is unknown. Could not check pending approvals: ${detail}. Retry the status check before continuing.`);
      }
    },
    [config],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const refreshOnFocus = () => void refresh();
    window.addEventListener("focus", refreshOnFocus);
    return () => window.removeEventListener("focus", refreshOnFocus);
  }, [refresh]);

  const request = useCallback(
    async (input: { pageId: string; newPath: string; newTitle: string }) => {
      if (pending) return;
      setPending(true);
      setError(null);
      try {
        const result = await requestWikiRenameApproval(config, input);
        if (!mounted.current) return;
        requestId.current += 1;
        reconciliationRequiredRef.current = false;
        setReconciliationRequired(false);
        setVerification("ready");
        requestFailure.current = null;
        setDraft(null);
        if (result.approval) {
          setApprovals((current) => [result.approval!, ...current.filter((approval) => approval.approvalId !== result.approval!.approvalId)]);
        } else {
          onResolved(result);
        }
        setError(null);
      } catch (reason) {
        if (mounted.current) {
          const detail = reason instanceof Error ? reason.message : String(reason);
          await reconcileFailedRequest(`Rename request failed: ${detail}`);
        }
      } finally {
        if (mounted.current) setPending(false);
      }
    },
    [config, onResolved, pending, reconcileFailedRequest],
  );

  const reconcile = useCallback(async () => {
    if (pending || !reconciliationRequiredRef.current) return;
    setPending(true);
    setError(null);
    try {
      if (requestFailure.current != null) {
        await reconcileFailedRequest(requestFailure.current);
      } else {
        setVerification("loading");
        const next = await listWikiRenameApprovals(config);
        if (!mounted.current) return;
        setApprovals(next);
        if (next.length > 0) setDraft(null);
        reconciliationRequiredRef.current = false;
        setReconciliationRequired(false);
        setVerification("ready");
        setError(null);
      }
    } catch (reason) {
      if (!mounted.current) return;
      const detail = reason instanceof Error ? reason.message : String(reason);
      setVerification("error");
      setError(`Couldn’t check pending rename approvals: ${detail}`);
    } finally {
      if (mounted.current) setPending(false);
    }
  }, [config, pending, reconcileFailedRequest]);

  const resolve = useCallback(
    async (approvalId: string, decision: "accept" | "reject") => {
      if (pending) return;
      setPending(true);
      setError(null);
      try {
        const result = decision === "accept" ? await acceptWikiRenameApproval(config, approvalId) : await rejectWikiRenameApproval(config, approvalId);
        if (!mounted.current) return;
        requestId.current += 1;
        if ((result.status === "pending" || result.status === "applying") && result.approval) {
          setApprovals((current) => [result.approval!, ...current.filter((approval) => approval.approvalId !== approvalId && approval.approvalId !== result.approval!.approvalId)]);
        } else {
          setApprovals((current) => current.filter((approval) => approval.approvalId !== approvalId));
          onResolved(result);
          void refresh();
        }
        setError(null);
      } catch (reason) {
        if (mounted.current) setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (mounted.current) setPending(false);
      }
    },
    [config, onResolved, pending, refresh],
  );

  const activeApproval = approvals[0] ?? null;
  const openDraft = useCallback((next: WikiRenameDraft) => {
    reconciliationRequiredRef.current = false;
    setReconciliationRequired(false);
    requestFailure.current = null;
    setError(null);
    setDraft(next);
  }, []);
  const cancelDraft = useCallback(() => {
    if (pending || reconciliationRequiredRef.current) return;
    setDraft(null);
    setError(null);
  }, [pending]);
  return {
    draft,
    openDraft,
    cancelDraft,
    activeApproval,
    pending,
    error,
    reconciliationRequired,
    verification,
    request,
    reconcile,
    resolve,
  };
}
