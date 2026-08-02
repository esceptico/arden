import { useState } from "react";
import { GitBranch, PencilEdit02 } from "@/components/icons";
import clsx from "clsx";
import { useStore } from "@/stores";
import { CopyGlyph } from "@/components/ui/CopyGlyph";
import { branchAtMessage } from "@/actions/sessions";
import { placeCaretAtEnd } from "@/features/chat/lib/mentionEditor";
import { ICON } from "@/lib/icons";
import { IconButton } from "@/components/ui/IconButton";
import { useTimeoutFlag } from "@/lib/hooks";
import { copyText } from "@/lib/clipboard";

function formatMessageTime(ms: number): string {
  const d = new Date(ms);
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const time = d.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
  if (sameDay) return time;
  const month = d.toLocaleString(undefined, { month: "short" });
  return `${month} ${d.getDate()} · ${time}`;
}

export function MessageActions({ id, role }: { id: string; role: "user" | "assistant" }) {
  const [copied, flashCopied] = useTimeoutFlag(1200);
  const [branching, setBranching] = useState(false);
  const startedAt = useStore((s) => s.messages.get(id)?.turn?.startedAt);

  async function copy() {
    const message = useStore.getState().messages.get(id);
    if (!message) return;
    // Only flash "Copied" if it actually landed — the bare bridge call would
    // resolve to undefined (no copy) yet still flash when the bridge is down.
    if (await copyText(message.content)) flashCopied();
  }

  function edit() {
    const message = useStore.getState().messages.get(id);
    if (!message) return;
    useStore.getState().setEditingId(id);
    useStore.getState().setDraft(message.content);
    requestAnimationFrame(() => {
      const input = document.querySelector<HTMLElement>("#message-input");
      if (!input) return;
      input.focus();
      placeCaretAtEnd(input);
    });
  }

  async function branch() {
    if (branching) return;
    setBranching(true);
    try {
      await branchAtMessage(id);
    } finally {
      setBranching(false);
    }
  }

  const timeLabel = startedAt && startedAt > 0 ? formatMessageTime(startedAt) : null;

  return (
    <div
      className={clsx(
        "board-message-actions flex items-center gap-1",
        role === "user" ? "board-message-actions--user justify-end" : "board-message-actions--assistant",
      )}
    >
      <IconButton
        size="md"
        tone="faint"
        onClick={copy}
        title={role === "user" ? "Copy message" : "Copy"}
        className={clsx(copied && "!text-ok hover:!text-ok")}
      >
        <CopyGlyph copied={copied} size={ICON.SM} />
      </IconButton>
      {role === "assistant" && (
        <IconButton
          size="md"
          tone="faint"
          onClick={() => void branch()}
          disabled={branching}
          title="Branch from this message"
        >
          <GitBranch size={ICON.SM} />
        </IconButton>
      )}
      {role === "user" && (
        <IconButton size="md" tone="faint" onClick={edit} title="Edit and resend">
          <PencilEdit02 size={ICON.SM} />
        </IconButton>
      )}
      {timeLabel && (
        <span
          className={clsx(
            "text-faint tracking-[var(--tracking-tight)] select-none",
            role === "user" ? "order-first mr-0.5 text-2xs" : "ml-0.5 text-xs",
          )}
        >
          {timeLabel}
        </span>
      )}
    </div>
  );
}
