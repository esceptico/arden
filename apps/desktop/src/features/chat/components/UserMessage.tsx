import { memo, useMemo } from "react";
import { Box, Target } from "@/components/icons";
import clsx from "clsx";
import { useStore } from "@/stores";
import type { SkillDescriptor } from "@/api/types";
import { viewSkill } from "@/actions/skills";
import { ICON } from "@/lib/icons";
import { MessageActions } from "@/features/chat/components/MessageActions";
import {
  SOURCE_FOCUS_CLASS,
  entryAnimation,
  useMessage,
  useSourceFocused,
} from "@/features/chat/lib/messageShared";

/** Detect a skill invocation in user content. Handles two formats:
 *
 *  1. Live (pre-server-expansion): `/skill-name <prompt>` — what the
 *     composer actually sends.
 *
 *  2. Historic (server-expanded): `<skill name="...">…body…</skill>\n\n
 *     User request: <prompt>` — what `expand_skill_command` writes into
 *     `sessions.messages` before saving.
 *
 *  Returns the matched skill descriptor + the user's actual prompt
 *  (everything after the skill block / slash command), or null. */
function detectSkillPrefix(
  content: string,
  skills: SkillDescriptor[],
): { skill: SkillDescriptor; rest: string } | null {
  // Format 1: /skill-name args
  if (content.startsWith("/")) {
    const slash = content.match(/^\/([\w-]+)\s*([\s\S]*)$/);
    if (slash) {
      const [, name, rest = ""] = slash;
      const skill = skills.find((s) => s.name === name);
      if (skill) return { skill, rest: rest.trimStart() };
    }
  }

  // Format 2: <skill name="..."> ... </skill>[\n\nUser request: ...]
  if (content.startsWith("<skill")) {
    const xml = content.match(
      /^<skill\s+name="([^"]+)"[^>]*>[\s\S]*?<\/skill>\s*(?:User request:\s*([\s\S]*))?$/,
    );
    if (xml) {
      const [, name, rest = ""] = xml;
      const skill = skills.find((s) => s.name === name);
      if (skill) return { skill, rest: rest.trim() };
    }
  }

  return null;
}

function SkillInlineToken({ skill }: { skill: SkillDescriptor }) {
  return (
    <button
      type="button"
      onClick={() => void viewSkill(skill.name)}
      title={skill.path ?? skill.name}
      className="inline-flex max-w-full items-baseline gap-1.5 align-baseline text-info hover:text-accent-strong transition-colors"
    >
      <Box size={ICON.SM} strokeWidth={2} className="relative top-[1px] shrink-0" />
      <span className="capitalize">{skill.name.replace(/[_-]/g, " ")}</span>
    </button>
  );
}

function GoalMessageBubble({ objective }: { objective: string }) {
  return (
    <div className="surface-panel surface-radius-lg max-w-[75%] px-3.5 py-2 text-left">
      <div className="mb-1 inline-flex items-center gap-1.5 text-2xs font-medium text-muted">
        <Target size={ICON.XS} strokeWidth={2} />
        <span>Goal</span>
      </div>
      <div className="whitespace-pre-wrap break-words text-base leading-[1.5] text-ink">
        {objective}
      </div>
    </div>
  );
}

export const UserMessage = memo(function UserMessage({ id }: { id: string }) {
  const message = useMessage(id);
  const skills = useStore((s) => s.skills);
  const sourceFocused = useSourceFocused(id);
  if (!message) return null;

  const skillMatch = useMemo(
    () => detectSkillPrefix(message.content, skills),
    [message.content, skills],
  );
  const goalMatch = useMemo(() => {
    const match = message.content.match(/^\/goal\s+([\s\S]+)$/);
    return match ? match[1].trim() : null;
  }, [message.content]);

  const visibleText = goalMatch ?? (skillMatch ? skillMatch.rest : message.content);
  const showBubble = visibleText.trim().length > 0 || Boolean(skillMatch);
  const images = message.images ?? [];

  return (
    <article
      className={clsx(
        "group flex flex-col items-end transition-[background-color,box-shadow] duration-panel",
        entryAnimation(message, "animate-fade-in"),
        sourceFocused && SOURCE_FOCUS_CLASS,
      )}
      data-id={id}
      data-source-focus={sourceFocused ? "true" : undefined}
      data-source-index={message.sourceIndex}
    >
      {images.length > 0 && (
        <div className="flex flex-wrap justify-end gap-1.5 max-w-[75%] mb-1.5">
          {images.map((img, i) => (
            <img
              key={i}
              src={`data:${img.media_type};base64,${img.data}`}
              alt=""
              className="rounded-lg max-h-[180px] max-w-[220px] object-cover outline outline-1 -outline-offset-1 outline-black/10 dark:outline-white/10"
            />
          ))}
        </div>
      )}
      {goalMatch ? (
        <GoalMessageBubble objective={goalMatch} />
      ) : showBubble && (
        <div className="surface-panel surface-radius-lg max-w-[75%] px-3.5 py-2 text-ink text-base leading-[1.5] break-words text-left">
          {skillMatch && (
            <>
              <SkillInlineToken skill={skillMatch.skill} />
              {visibleText.trim().length > 0 ? " " : null}
            </>
          )}
          <span className="whitespace-pre-wrap">{visibleText}</span>
        </div>
      )}
      <MessageActions id={id} role="user" />
    </article>
  );
});
