import type { InspectedSource } from "@/features/sources/lib/sourceInspector";
import type { ActivityItem, SourceRef } from "@/stores";

export interface SourceCallGroup {
  key: string;
  toolCallId?: string;
  toolCall?: ActivityItem;
  target: string;
  sources: SourceRef[];
  sourceCount: number;
}

export interface SourceActionGroup {
  key: string;
  label: string;
  calls: SourceCallGroup[];
  callCount: number;
  sourceCount: number;
}

export interface SourceProviderGroup {
  key: string;
  provider: string;
  actions: SourceActionGroup[];
  callCount: number;
  sourceCount: number;
}

interface ActionBuilder extends SourceActionGroup {
  callsByKey: Map<string, SourceCallGroup>;
}

interface ProviderBuilder extends SourceProviderGroup {
  actions: ActionBuilder[];
  actionsByKey: Map<string, ActionBuilder>;
}

export function groupInspectedSources(items: InspectedSource[]): SourceProviderGroup[] {
  const providers = new Map<string, ProviderBuilder>();

  for (const { source, toolCall } of items) {
    if (toolCall?.error) continue;

    let provider = providers.get(source.provider);
    if (!provider) {
      provider = {
        key: source.provider,
        provider: source.provider,
        actions: [],
        actionsByKey: new Map(),
        callCount: 0,
        sourceCount: 0,
      };
      providers.set(source.provider, provider);
    }

    const actionToken = toolCall?.kind ?? toolCall?.noun ?? toolCall?.displayName ?? "source-action";
    const actionKey = `${toolCall?.source ?? source.provider}\u0000${actionToken}`;
    let action = provider.actionsByKey.get(actionKey);
    if (!action) {
      action = {
        key: `${source.provider}\u0000${actionKey}`,
        label: toolCall?.displayName ?? toolCall?.noun ?? toolCall?.kind ?? "Source action",
        calls: [],
        callsByKey: new Map(),
        callCount: 0,
        sourceCount: 0,
      };
      provider.actionsByKey.set(actionKey, action);
      provider.actions.push(action);
    }

    const toolCallId = source.toolCallId ?? toolCall?.id;
    const callKey = toolCallId ?? `source\u0000${source.ref}`;
    let call = action.callsByKey.get(callKey);
    if (!call) {
      call = {
        key: `${action.key}\u0000${callKey}`,
        ...(toolCallId ? { toolCallId } : {}),
        ...(toolCall ? { toolCall } : {}),
        target: toolCall?.target?.trim() || action.label,
        sources: [],
        sourceCount: 0,
      };
      action.callsByKey.set(callKey, call);
      action.calls.push(call);
      action.callCount += 1;
      provider.callCount += 1;
    }

    call.sources.push(source);
    call.sourceCount += 1;
    action.sourceCount += 1;
    provider.sourceCount += 1;
  }

  return Array.from(providers.values(), ({ actionsByKey: _actionsByKey, ...provider }) => ({
    ...provider,
    actions: provider.actions.map(({ callsByKey: _callsByKey, ...action }) => action),
  }));
}
