import { Briefcase, FileSearch, GitPullRequest, Inbox, Mail, type ArdenIcon } from "@/components/icons";
import type { CreateAutomationPayload } from "@/api/types";

/** Starter automations the user can pick to bootstrap a new automation
 *  without having to think up the prompt + schedule from scratch.
 *  Each template is a thin shell over `CreateAutomationPayload` plus a
 *  small visual card. Clicking one drops the user into the editor with
 *  the fields pre-filled, no commit until they hit Create. */
export interface AutomationTemplate {
  id: string;
  category: string;
  icon: ArdenIcon;
  name: string;
  blurb: string;
  payload: CreateAutomationPayload;
}

export const TEMPLATES: AutomationTemplate[] = [
  {
    id: "daily-standup",
    category: "Status reports",
    icon: Briefcase,
    name: "Daily standup",
    blurb: "Open tasks, blockers, and what to ship today.",
    payload: {
      name: "Daily standup",
      description: "Summarizes open work, blockers, and today’s highest-priority deliveries.",
      prompt:
        "Pull my open tasks and highlight blockers. Suggest the two or three things I should ship today, in priority order. Quote PR titles / numbers verbatim when relevant.",
      trigger_type: "time",
      at: "09:00",
      days: "weekdays",
    },
  },
  {
    id: "weekly-pr",
    category: "Status reports",
    icon: GitPullRequest,
    name: "Weekly PR summary",
    blurb: "Last week's PRs grouped by teammate and theme.",
    payload: {
      name: "Weekly PR summary",
      description: "Groups last week’s pull requests by teammate, theme, and delivery risk.",
      prompt:
        "Summarize last week's PRs by teammate and theme. Highlight risky merges or anything still in review. Use PR numbers / titles when available.",
      trigger_type: "time",
      at: "09:00",
      days: "Mon",
    },
  },
  {
    id: "stale-sweep",
    category: "Cleanup",
    icon: FileSearch,
    name: "Stale issues sweep",
    blurb: "Top 10 issues with no activity in 14+ days.",
    payload: {
      name: "Stale issues sweep",
      description: "Surfaces the most important issues that have gone quiet for two weeks.",
      prompt:
        "Find issues older than 14 days with no activity. Surface the top ten with brief context for why they may have stalled.",
      trigger_type: "time",
      at: "16:00",
      days: "Fri",
    },
  },
  {
    id: "inbox-triage",
    category: "Inbox",
    icon: Inbox,
    name: "Inbox triage",
    blurb: "Buckets unread email by priority.",
    payload: {
      name: "Inbox triage",
      description: "Sorts unread email into clear priority buckets for quick review.",
      prompt:
        "Triage my unread email since yesterday. Bucket by priority (urgent / today / this week / fyi). Quote the first sentence of each so I can scan fast.",
      trigger_type: "time",
      at: "08:00",
      days: "weekdays",
    },
  },
  {
    id: "calendar-prep",
    category: "Calendar",
    icon: Mail,
    name: "Meeting prep",
    blurb: "Brief me 15 minutes before each meeting.",
    payload: {
      name: "Meeting prep",
      description: "Prepares the people, agenda, and recent context before each meeting.",
      prompt:
        "Brief me on the upcoming meeting: who's there, the agenda, and recent context I should walk in with.",
      trigger_type: "event",
      event_type: "approaching",
      lead_minutes: 15,
    },
  },
];
