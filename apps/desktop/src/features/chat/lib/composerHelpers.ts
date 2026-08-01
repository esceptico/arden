import { type ImageBlock } from "@/stores";

/** Read a single File and return its bytes as base64 + media type. */
export function fileToImageBlock(file: File): Promise<ImageBlock> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("Read failed"));
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        reject(new Error("Unexpected reader result"));
        return;
      }
      // result is "data:<media_type>;base64,<data>"
      const [meta, data] = result.split(",", 2);
      const m = meta.match(/^data:([^;]+);base64$/);
      resolve({ media_type: m?.[1] ?? file.type ?? "application/octet-stream", data: data ?? "" });
    };
    reader.readAsDataURL(file);
  });
}

/** The /name token being composed at the caret, if any. A mention starts
 *  the message or follows whitespace (never mid-word or mid-path), and the
 *  picker stays open only while the name is still being typed (no space
 *  between the slash and the caret). `start === 0` is the classic
 *  start-of-message command; anything else is a mid-text skill mention. */
export function mentionQueryAt(
  text: string,
  caret: number,
): { query: string; start: number } | null {
  const head = text.slice(0, caret);
  const slash = head.lastIndexOf("/");
  if (slash === -1) return null;
  if (slash > 0 && !/\s/.test(head[slash - 1]!)) return null;
  const token = head.slice(slash + 1);
  if (/\s/.test(token)) return null;
  return { query: token, start: slash };
}

export function resize(input: HTMLTextAreaElement) {
  input.style.height = "0px";
  input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
}
