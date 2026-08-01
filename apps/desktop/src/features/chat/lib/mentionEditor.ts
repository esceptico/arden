import type { SkillDescriptor } from "@/api/types";
import { splitSkillMentions } from "@/features/chat/lib/skillMentions";

/** DOM helpers for the composer's contenteditable editor. The editor's
 *  document is plain text plus atomic mention tokens:
 *
 *    text node            → its characters
 *    [data-mention="x"]   → "/x" (contenteditable=false — caret skips it,
 *                            backspace removes it whole)
 *    <br>                 → "\n"
 *
 *  `serializeEditor` is the single source of truth for the draft string;
 *  everything else (mention queries, history recall, submit) works on
 *  that string with offsets mapped back into the DOM here. */

const BOX_ICON_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
  '<path d="M12 22C11.1818 22 10.4002 21.6698 8.83693 21.0095C4.94564 19.3657 3 18.5438 3 17.1613C3 16.7742 3 10.0645 3 7M12 22C12.8182 22 13.5998 21.6698 15.1631 21.0095C19.0544 19.3657 21 18.5438 21 17.1613V7M12 22L12 11.3548" stroke-linecap="round" stroke-linejoin="round"/>' +
  '<path d="M8.32592 9.69138L5.40472 8.27785C3.80157 7.5021 3 7.11423 3 6.5C3 5.88577 3.80157 5.4979 5.40472 4.72215L8.32592 3.30862C10.1288 2.43621 11.0303 2 12 2C12.9697 2 13.8712 2.4362 15.6741 3.30862L18.5953 4.72215C20.1984 5.4979 21 5.88577 21 6.5C21 7.11423 20.1984 7.5021 18.5953 8.27785L15.6741 9.69138C13.8712 10.5638 12.9697 11 12 11C11.0303 11 10.1288 10.5638 8.32592 9.69138Z" stroke-linecap="round" stroke-linejoin="round"/>' +
  '<path d="M6 12L8 13" stroke-linecap="round" stroke-linejoin="round"/>' +
  '<path d="M17 4L7 9" stroke-linecap="round" stroke-linejoin="round"/>' +
  "</svg>";

function escapeHtml(text: string): string {
  return text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
}

/** Inline token markup — the same form as SkillInlineToken in sent
 *  bubbles: box icon + capitalized name in accent ink, nothing else. */
export function mentionHtml(skill: SkillDescriptor): string {
  const label = skill.name.replace(/[_-]/g, " ");
  return (
    `<span class="composer-mention" data-mention="${escapeHtml(skill.name)}" contenteditable="false">` +
    `${BOX_ICON_SVG}<span class="composer-mention__label">${escapeHtml(label)}</span></span>`
  );
}

export function serializeEditor(root: HTMLElement | DocumentFragment): string {
  let out = "";
  let lastWasBr = false;
  const visit = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const value = node.nodeValue ?? "";
      out += value;
      if (value.length > 0) lastWasBr = false;
      return;
    }
    if (node instanceof HTMLElement && node.dataset.mention) {
      out += `/${node.dataset.mention}`;
      lastWasBr = false;
      return;
    }
    if (node.nodeName === "BR") {
      out += "\n";
      lastWasBr = true;
      return;
    }
    node.childNodes.forEach(visit);
  };
  root.childNodes.forEach(visit);
  // A trailing <br> is Chrome's placeholder that keeps the last line
  // visible/editable, not content — "text<br>" is "text", "text<br><br>"
  // is "text\n", and a lone <br> is an empty editor.
  return lastWasBr ? out.slice(0, -1) : out;
}

/** Caret position as an offset into the serialized string. Falls back to
 *  the end when the selection lives outside the editor. */
export function caretOffset(root: HTMLElement): number {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return serializeEditor(root).length;
  const range = sel.getRangeAt(0);
  if (!root.contains(range.endContainer)) return serializeEditor(root).length;
  const pre = document.createRange();
  pre.selectNodeContents(root);
  pre.setEnd(range.endContainer, range.endOffset);
  return serializeEditor(pre.cloneContents()).length;
}

function domPosition(root: HTMLElement, target: number): { node: Node; offset: number } {
  let acc = 0;
  let found: { node: Node; offset: number } | null = null;
  const boundaryAfter = (node: Node, consumed: boolean) => {
    const parent = node.parentNode!;
    const index = Array.prototype.indexOf.call(parent.childNodes, node);
    return { node: parent, offset: index + (consumed ? 1 : 0) };
  };
  const visit = (node: Node): boolean => {
    if (node.nodeType === Node.TEXT_NODE) {
      const len = node.nodeValue?.length ?? 0;
      if (acc + len >= target) {
        found = { node, offset: target - acc };
        return true;
      }
      acc += len;
      return false;
    }
    if (node instanceof HTMLElement && node.dataset.mention) {
      const len = node.dataset.mention.length + 1;
      if (acc + len >= target) {
        // Atomic: any offset inside the token resolves to its edges.
        found = boundaryAfter(node, target > acc);
        return true;
      }
      acc += len;
      return false;
    }
    if (node.nodeName === "BR") {
      if (acc + 1 >= target) {
        found = boundaryAfter(node, target > acc);
        return true;
      }
      acc += 1;
      return false;
    }
    for (const child of Array.from(node.childNodes)) if (visit(child)) return true;
    return false;
  };
  visit(root);
  return found ?? { node: root, offset: root.childNodes.length };
}

export function selectSerializedRange(root: HTMLElement, start: number, end: number): void {
  const sel = window.getSelection();
  if (!sel) return;
  const a = domPosition(root, start);
  const b = domPosition(root, end);
  const range = document.createRange();
  range.setStart(a.node, a.offset);
  range.setEnd(b.node, b.offset);
  sel.removeAllRanges();
  sel.addRange(range);
}

export function placeCaretAtEnd(root: HTMLElement): void {
  const sel = window.getSelection();
  if (!sel) return;
  const range = document.createRange();
  range.selectNodeContents(root);
  range.collapse(false);
  sel.removeAllRanges();
  sel.addRange(range);
}

/** Replace the serialized range [start, end) with an atomic mention token
 *  plus a trailing space, keeping the browser's undo stack intact. */
export function insertMention(root: HTMLElement, start: number, end: number, skill: SkillDescriptor): void {
  root.focus();
  selectSerializedRange(root, start, end);
  document.execCommand("insertHTML", false, `${mentionHtml(skill)} `);
}

/** Rebuild the editor's DOM from a draft string, tokenizing every known
 *  mention. Programmatic writes only (history recall, edit, clear) — the
 *  browser owns the DOM while the user types. */
export function renderDraft(root: HTMLElement, draft: string, skills: SkillDescriptor[]): void {
  const segments = splitSkillMentions(draft, skills);
  if (!segments) {
    root.textContent = draft;
    return;
  }
  root.innerHTML = segments
    .map((segment) => (typeof segment === "string" ? escapeHtml(segment) : mentionHtml(segment)))
    .join("");
}
