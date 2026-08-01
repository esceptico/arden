import { memo, useMemo } from "react";
import { Box, Target } from "@/components/icons";
import clsx from "clsx";
import { useStore } from "@/stores";
import { Markdown } from "@/components/ui/Markdown";
import type { SkillDescriptor } from "@/api/types";
import { viewSkill } from "@/actions/skills";
import { splitSkillMentions } from "@/features/chat/lib/skillMentions";
import { ICON } from "@/lib/icons";
import { MessageActions } from "@/features/chat/components/MessageActions";
import {
  SOURCE_FOCUS_CLASS,
  entryAnimation,
  useMessage,
  useSourceFocused,
} from "@/features/chat/lib/messageShared";

/** Historic (server-expanded) format: `<skill name="...">…body…</skill>\n\n
 *  User request: <prompt>` — what the old start-of-message expansion wrote
 *  into `sessions.messages` before mentions moved to prompt assembly. */
function detectExpandedSkill(
  content: string,
  skills: SkillDescriptor[],
): { skill: SkillDescriptor; rest: string } | null {
  if (!content.startsWith("<skill")) return null;
  const xml = content.match(
    /^<skill\s+name="([^"]+)"[^>]*>[\s\S]*?<\/skill>\s*(?:User request:\s*([\s\S]*))?$/,
  );
  if (!xml) return null;
  const [, name, rest = ""] = xml;
  const skill = skills.find((s) => s.name === name);
  return skill ? { skill, rest: rest.trim() } : null;
}

function SkillInlineToken({ skill }: { skill: SkillDescriptor }) {
  return (
    <button
      type="button"
      onClick={() => void viewSkill(skill.name)}
      title={skill.path ?? skill.name}
      className="inline-flex max-w-full items-baseline gap-1.5 align-baseline text-info font-medium hover:text-accent-strong transition-colors"
    >
      <Box size={ICON.SM} strokeWidth={2} className="relative top-[1px] shrink-0" />
      <span className="capitalize underline decoration-[color-mix(in_oklab,currentColor_30%,transparent)] underline-offset-2 hover:decoration-current">
        {skill.name.replace(/[_-]/g, " ")}
      </span>
    </button>
  );
}

function GoalMessageBubble({ objective }: { objective: string }) {
  return (
    <div className="board-user__bubble board-user__goal surface-panel text-left">
      <div className="mb-1 inline-flex items-center gap-1.5 text-2xs font-medium text-muted">
        <Target size={ICON.XS} strokeWidth={2} />
        <span>Goal</span>
      </div>
      <div className="whitespace-pre-wrap break-words text-ink">
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
    () => detectExpandedSkill(message.content, skills),
    [message.content, skills],
  );
  const mentionSegments = useMemo(
    () => (skillMatch ? null : splitSkillMentions(message.content, skills)),
    [message.content, skillMatch, skills],
  );
  const goalMatch = useMemo(() => {
    const match = message.content.match(/^\/goal\s+([\s\S]+)$/);
    return match ? match[1].trim() : null;
  }, [message.content]);

  const visibleText = goalMatch ?? (skillMatch ? skillMatch.rest : message.content);
  const showBubble = visibleText.trim().length > 0 || Boolean(skillMatch) || Boolean(mentionSegments);
  // The anchor is the whole article, so textContent would sweep in the action
  // row's timestamp — the rail label has to be the prompt itself.
  const railLabel = visibleText.trim().replace(/\s+/g, " ");
  const images = message.images ?? [];
  const hasContent = images.length > 0 || Boolean(goalMatch) || showBubble;

  return (
    <article
      className={clsx(
        "board-user group flex flex-col items-end transition-[background-color,box-shadow] duration-panel",
        entryAnimation(message, "animate-fade-in"),
        sourceFocused && SOURCE_FOCUS_CLASS,
      )}
      data-id={id}
      data-chat-rail-anchor
      data-chat-rail-label={railLabel || undefined}
      data-source-focus={sourceFocused ? "true" : undefined}
      data-source-index={message.sourceIndex}
    >
      {hasContent && (
        <div className="board-user__content flex max-w-full flex-col items-end">
          {images.length > 0 && (
            <div className="board-user__images flex max-w-[75%] flex-wrap justify-end gap-1.5">
              {images.map((img, i) => (
                <img
                  key={i}
                  src={`data:${img.media_type};base64,${img.data}`}
                  alt=""
                  className="max-h-[180px] max-w-[220px] rounded-[var(--r-house)] object-cover outline outline-1 -outline-offset-1 outline-black/10 dark:outline-white/10"
                />
              ))}
            </div>
          )}
          {goalMatch ? (
            <GoalMessageBubble objective={goalMatch} />
          ) : showBubble && (
            <div className="board-user__bubble surface-panel text-left text-ink break-words">
              {/* Historic server-expanded messages keep the token-above-prose
                  layout; live mention messages render tokens inline, exactly
                  where they sit in the sentence. */}
              {skillMatch && (
                <div className={visibleText.trim().length > 0 ? "mb-1" : undefined}>
                  <SkillInlineToken skill={skillMatch.skill} />
                </div>
              )}
              {mentionSegments ? (
                <div className="whitespace-pre-wrap">
                  {mentionSegments.map((segment, i) =>
                    typeof segment === "string" ? (
                      <span key={i}>{segment}</span>
                    ) : (
                      <SkillInlineToken key={i} skill={segment} />
                    ),
                  )}
                </div>
              ) : (
                <Markdown
                  content={visibleText}
                  codeChrome={false}
                  className="typeset-verbatim"
                />
              )}
            </div>
          )}
        </div>
      )}
      <MessageActions id={id} role="user" />
    </article>
  );
});
