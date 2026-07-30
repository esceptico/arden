import {
  createAutomationApi,
  deleteAutomationApi,
  generateAutomationDescriptionApi,
  listAutomationsApi,
  runAutomationApi,
  toggleAutomationApi,
  updateAutomationApi,
} from "@/api/automations";
import type { Automation, AutomationTrigger, CreateAutomationPayload, UpdateAutomationPayload } from "@/api/types";
import { getState } from "@/stores";

export async function fetchAutomations(): Promise<void> {
  const s = getState();
  try {
    const automations = await listAutomationsApi(s.config);
    s.setAutomations(automations);
  } catch {
    /* leave previous list in place */
  }
}

export async function createAutomation(payload: CreateAutomationPayload): Promise<Automation> {
  const s = getState();
  const automation = await createAutomationApi(s.config, payload);
  await fetchAutomations();
  return automation;
}

function duplicateTrigger(trigger: AutomationTrigger): AutomationTrigger {
  if (trigger.type !== "message") return { ...trigger };

  // The server deliberately resolves names again on create. The list response
  // holds Slack ids too, which are not accepted as the editor-facing input.
  const fromUser = trigger.from_user_name ?? trigger.from_user;
  return {
    type: "message",
    source: trigger.source ?? "slack",
    channels: trigger.channels?.map((channel) =>
      typeof channel === "string" ? channel : channel.name,
    ),
    ...(fromUser ? { from_user: fromUser } : {}),
    ...(trigger.contains?.length ? { contains: [...trigger.contains] } : {}),
  };
}

/** Build a real create payload from the server's automation representation.
 * This lives at the action boundary so every duplicate preserves the same
 * trigger and capability contract instead of rebuilding it in a menu. */
export function duplicateAutomationPayload(
  source: Automation,
  idempotencyKey: string = crypto.randomUUID(),
): CreateAutomationPayload {
  const duplicateableTriggers = source.triggers.filter((trigger) => (
    ["time", "event", "idle", "count", "message"].includes(trigger.type)
  ));
  if (duplicateableTriggers.length === 0) {
    throw new Error("This automation has no duplicable trigger.");
  }
  return {
    name: `${source.name.trim() || "Untitled automation"} copy`,
    prompt: source.prompt,
    idempotency_key: idempotencyKey,
    idempotency_scope: "global",
    ...(source.description ? { description: source.description } : {}),
    model: source.model,
    auto_approve: source.auto_approve,
    triggers: duplicateableTriggers.map(duplicateTrigger),
    cooldown_minutes: source.cooldown_minutes,
    tool_scope: source.tool_scope,
  };
}

export async function duplicateAutomation(
  source: Automation,
  idempotencyKey?: string,
): Promise<Automation> {
  return createAutomation(duplicateAutomationPayload(source, idempotencyKey));
}

export async function updateAutomation(taskId: string, patch: UpdateAutomationPayload): Promise<Automation> {
  const s = getState();
  const automation = await updateAutomationApi(s.config, taskId, patch);
  await fetchAutomations();
  return automation;
}

export async function generateAutomationDescription(taskId: string): Promise<Automation> {
  const s = getState();
  const automation = await generateAutomationDescriptionApi(s.config, taskId);
  await fetchAutomations();
  return automation;
}

export async function toggleAutomation(taskId: string): Promise<void> {
  const s = getState();
  await toggleAutomationApi(s.config, taskId);
  await fetchAutomations();
}

export async function runAutomation(taskId: string): Promise<void> {
  const s = getState();
  await runAutomationApi(s.config, taskId);
  await fetchAutomations();
}

export async function deleteAutomation(taskId: string): Promise<void> {
  const s = getState();
  await deleteAutomationApi(s.config, taskId);
  await fetchAutomations();
}
