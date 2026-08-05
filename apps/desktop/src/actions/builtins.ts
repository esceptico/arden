import { apiWithConfig, compactSessionApi } from "@/api/core";
import { listPrimarySessionsApi } from "@/api/sessions";
import type { SessionListItem } from "@/api/types";
import { getState } from "@/stores";
import { forgetEventSeqForSession } from "@/stores/chat-stream";
import { loadHistory } from "@/actions/history";
import { enqueueMessage, sendMessage } from "@/actions/messages";
import { switchSession } from "@/actions/sessions";
import { clearGoal, proposeGoal, setGoal, updateGoal } from "@/actions/goals";
import { appendError, appendStatus, formatCost, formatTokens } from "@/actions/_shared";

export interface BuiltinCommand {
  name: string;
  description: string;
  /** Hidden commands aren't surfaced in the picker but are still routable
   *  via dispatchCommand when typed manually. Reserved for things that
   *  require an arg (e.g. /rename) so they don't muddy the visual list. */
  hidden?: boolean;
}

export const BUILTIN_COMMANDS: BuiltinCommand[] = [
  { name: "clear", description: "Clear this session's messages" },
  { name: "compact", description: "Compact context window" },
  { name: "revert", description: "Revert one turn" },
  { name: "branch", description: "Branch into a new session" },
  { name: "rename", description: "Rename this session", hidden: true },
  { name: "cost", description: "Show usage so far" },
  { name: "goal", description: "Set or show this session's goal" },
];

const BUILTIN_NAMES = new Set(BUILTIN_COMMANDS.map((c) => c.name));

export function isBuiltin(name: string): boolean {
  return BUILTIN_NAMES.has(name);
}

export async function runBuiltinCommand(name: string, args: string): Promise<void> {
  const s = getState();
  switch (name) {
    case "cost": {
      const u = s.usage;
      appendStatus(
        `Current context: ${formatTokens(u.contextInputTokens)} tokens · Observed: ${formatTokens(u.totalTokens)} tokens · ${formatCost(u.totalCost)}`,
      );
      return;
    }
    case "goal": {
      const arg = args.trim();
      if (arg === "pause") {
        await updateGoal("paused");
        return;
      }
      if (arg === "resume") {
        await updateGoal("active");
        return;
      }
      if (arg === "clear") {
        await clearGoal();
        return;
      }
      if (arg === "complete") {
        await updateGoal("complete");
        return;
      }
      if (!arg) {
        await proposeGoal();
        return;
      }
      const goal = await setGoal(arg);
      if (arg && goal) {
        const prompt = `/goal ${goal.objective}`;
        if (s.running) {
          await enqueueMessage(prompt);
        } else {
          await sendMessage(prompt);
        }
      }
      return;
    }
    case "clear": {
      if (!s.currentSessionId) return;
      try {
        await apiWithConfig(s.config, "/session/clear", {
          method: "POST",
          body: JSON.stringify({ session_id: s.currentSessionId }),
        });
        forgetEventSeqForSession(s.currentSessionId);
        s.setHistory([]);
        s.resetUsage();
      } catch (error) {
        appendError(error instanceof Error ? error.message : String(error));
      }
      return;
    }
    case "compact": {
      if (!s.currentSessionId) return;
      try {
        const result = await compactSessionApi(s.config, s.currentSessionId);
        if (result.status === "compacted") {
          await loadHistory(s.currentSessionId);
          appendStatus(result.message ?? "Context compacted.");
        } else {
          appendStatus(result.message ?? "Context below compaction threshold.");
        }
      } catch (error) {
        appendError(error instanceof Error ? error.message : String(error));
      }
      return;
    }
    case "revert": {
      if (!s.currentSessionId) return;
      const n = parseInt(args, 10);
      const turns = Number.isFinite(n) && n > 0 ? n : 1;
      try {
        await apiWithConfig(s.config, "/session/revert", {
          method: "POST",
          body: JSON.stringify({ session_id: s.currentSessionId, turns }),
        });
        await loadHistory(s.currentSessionId);
        appendStatus(`Reverted ${turns} turn${turns === 1 ? "" : "s"}.`);
      } catch (error) {
        appendError(error instanceof Error ? error.message : String(error));
      }
      return;
    }
    case "rename": {
      if (!s.currentSessionId) return;
      const name = args.trim();
      if (!name) {
        appendError("Usage: /rename <name>");
        return;
      }
      try {
        await apiWithConfig(s.config, `/sessions/${s.currentSessionId}`, {
          method: "PATCH",
          body: JSON.stringify({ name }),
        });
        s.setSessions(
          s.sessions.map((sess) =>
            sess.session_id === s.currentSessionId ? { ...sess, name } : sess,
          ),
        );
        appendStatus(`Renamed to "${name}".`);
      } catch (error) {
        appendError(error instanceof Error ? error.message : String(error));
      }
      return;
    }
    case "branch": {
      if (!s.currentSessionId) return;
      const name = args.trim();
      try {
        const branched = await apiWithConfig<SessionListItem>(
          s.config,
          `/sessions/${s.currentSessionId}/branch`,
          {
            method: "POST",
            body: JSON.stringify(name ? { name } : {}),
          },
        );
        s.setSessions(await listPrimarySessionsApi(s.config));
        await switchSession(branched.session_id);
      } catch (error) {
        appendError(error instanceof Error ? error.message : String(error));
      }
      return;
    }
  }
}
