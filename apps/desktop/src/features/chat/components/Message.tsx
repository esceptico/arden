import { useStore } from "@/stores";
import { UserMessage } from "@/features/chat/components/UserMessage";
import { AssistantMessage } from "@/features/chat/components/AssistantMessage";
import { ReasoningMessage } from "@/features/chat/components/ReasoningMessage";
import { ToolMessage, StatusMessage, ErrorMessage } from "@/features/chat/components/SimpleMessages";
import { ActivityMessage } from "@/features/chat/components/ActivityMessage";
import { TodoMessage } from "@/features/chat/components/TodoMessage";

export function Message({
  id,
  isFinal = true,
  sourceTurnId,
  sourceCount,
}: {
  id: string;
  isFinal?: boolean;
  sourceTurnId?: string;
  sourceCount?: number;
}) {
  const role = useStore((s) => s.messages.get(id)?.role);
  if (!role) return null;
  switch (role) {
    case "user": return <UserMessage id={id} />;
    case "assistant": return (
      <AssistantMessage
        id={id}
        isFinal={isFinal}
        sourceTurnId={sourceTurnId}
        sourceCount={sourceCount}
      />
    );
    case "reasoning": return <ReasoningMessage id={id} />;
    case "tool": return <ToolMessage id={id} />;
    case "activity": return <ActivityMessage id={id} />;
    case "todo": return <TodoMessage id={id} />;
    case "error": return <ErrorMessage id={id} />;
    case "status": return <StatusMessage id={id} />;
  }
}
