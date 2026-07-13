import { describe, expect, test } from "bun:test";
import {
  automationEventsUrl,
  memoryVaultChangeFromEvent,
  reduceAutomationStreamCursor,
} from "@/features/automations/hooks/useAutomationEvents";
import { useStore } from "@/stores";
import { MEMORY_VAULT_CHANGE_CAP } from "@/stores/memory-vault-domain";

describe("automation event stream helpers", () => {
  test("automationEventsUrl resumes from the last seen seq", () => {
    expect(automationEventsUrl("http://127.0.0.1:8000", undefined)).toBe(
      "http://127.0.0.1:8000/automations/events",
    );
    expect(automationEventsUrl("http://127.0.0.1:8000", 42)).toBe(
      "http://127.0.0.1:8000/automations/events?after_seq=42",
    );
    expect(automationEventsUrl("http://127.0.0.1:8000/api", 42)).toBe(
      "http://127.0.0.1:8000/api/automations/events?after_seq=42",
    );
  });

  test("reduceAutomationStreamCursor advances on event seq and keepalive latest seq", () => {
    expect(reduceAutomationStreamCursor(undefined, { type: "automation_progress", seq: 2 })).toBe(2);
    expect(reduceAutomationStreamCursor(2, { type: "stream_keepalive", latest_seq: 9 })).toBe(9);
    expect(reduceAutomationStreamCursor(9, { type: "automation_finished", seq: 4 })).toBe(9);
  });

  test("memory change keeps exact paths, revision, review flag, and sequence", () => {
    expect(memoryVaultChangeFromEvent({
      type: "memory_changed",
      paths: ["topics/a.md"],
      revision: "sha256:r2",
      review_required: true,
      seq: 17,
    })).toEqual({
      paths: ["topics/a.md"], revision: "sha256:r2", reviewRequired: true, seq: 17,
    });
  });

  test("memory store retains sequenced events and ignores duplicate or evicted-stale delivery", () => {
    const before = useStore.getState().memoryVaultVersion;
    useStore.setState({ memoryVaultChanges: [] });
    const change = { paths: ["topics/a.md"], revision: "sha256:r2", reviewRequired: true, seq: 17 };
    useStore.getState().memoryVaultChanged(change);
    expect(useStore.getState().memoryVaultChanges).toEqual([change]);
    expect(useStore.getState().memoryVaultVersion).toBe(before + 1);
    useStore.getState().memoryVaultChanged(change);
    expect(useStore.getState().memoryVaultVersion).toBe(before + 1);
    useStore.getState().memoryVaultChanged({ ...change, revision: "sha256:older", seq: 16 });
    expect(useStore.getState().memoryVaultChanges.map((candidate) => candidate.seq)).toEqual([16, 17]);
    expect(useStore.getState().memoryVaultVersion).toBe(before + 2);
  });

  test("memory changes are sequence ordered, deduplicated, and deterministically bounded", () => {
    useStore.setState({ memoryVaultChanges: [] });
    for (let seq = MEMORY_VAULT_CHANGE_CAP + 12; seq >= 1; seq -= 1) {
      useStore.getState().memoryVaultChanged({
        paths: [`topics/${seq}.md`], revision: `r${seq}`, reviewRequired: false, seq,
      });
    }
    useStore.getState().memoryVaultChanged({
      paths: ["duplicate.md"], revision: "duplicate", reviewRequired: true, seq: MEMORY_VAULT_CHANGE_CAP,
    });

    const changes = useStore.getState().memoryVaultChanges;
    expect(changes).toHaveLength(MEMORY_VAULT_CHANGE_CAP);
    expect(changes.map((change) => change.seq)).toEqual(
      Array.from({ length: MEMORY_VAULT_CHANGE_CAP }, (_, index) => index + 13),
    );
    expect(changes.find((change) => change.seq === MEMORY_VAULT_CHANGE_CAP)?.revision).toBe(`r${MEMORY_VAULT_CHANGE_CAP}`);
  });
});
