import { useRef, useState } from "react";
import clsx from "clsx";
import { Calendar, Hash, List, SquareCheck, Tag, Text, type ArdenIcon } from "@/components/icons";

export type MemoryFrontmatterValue = string | number | boolean | null | Array<string | number | boolean | null>;
export type MemoryFrontmatter = Record<string, MemoryFrontmatterValue>;

// arden-maintained fields — kept in the file, hidden from the Properties UI.
const SYSTEM_PROPS = new Set(["type", "updated"]);

type PropKind = "tags" | "list" | "checkbox" | "number" | "date" | "text";

const PROP_ICONS: Record<PropKind, ArdenIcon> = {
  tags: Tag,
  list: List,
  checkbox: SquareCheck,
  number: Hash,
  date: Calendar,
  text: Text,
};

function propType(key: string, value: MemoryFrontmatterValue): PropKind {
  if (key === "tags" || key === "aliases") return "tags";
  if (Array.isArray(value)) return "list";
  if (typeof value === "boolean") return "checkbox";
  if (typeof value === "number") return "number";
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}/.test(value)) return "date";
  return "text";
}

function parsePropInput(raw: string): MemoryFrontmatterValue {
  const v = raw.trim();
  if (/^(true|yes)$/i.test(v)) return true;
  if (/^(false|no)$/i.test(v)) return false;
  if (/^-?\d+$/.test(v)) return parseInt(v, 10);
  if (/^-?\d+\.\d+$/.test(v)) return parseFloat(v);
  if (v.includes(",")) return v.split(",").map((x) => x.trim()).filter(Boolean);
  return v;
}

function PropIcon({ kind }: { kind: PropKind }) {
  const Icon = PROP_ICONS[kind];
  return (
    <span className="mw-prop-icon">
      <Icon size={13.5} strokeWidth={1.7} aria-hidden />
    </span>
  );
}

// Obsidian-style typed frontmatter grid under the note title (draft's propsHtml).
export function MemoryProperties({
  frontmatter,
  editable,
  onChange,
}: {
  frontmatter: MemoryFrontmatter;
  editable: boolean;
  onChange?: (next: MemoryFrontmatter) => void;
}) {
  const [edit, setEdit] = useState<{ key: string; mode: "edit" | "append" } | null>(null);
  const [adding, setAdding] = useState(false);
  const addKeyRef = useRef<HTMLInputElement>(null);
  const addValueRef = useRef<HTMLInputElement>(null);

  const entries = Object.entries(frontmatter).filter(([key]) => !SYSTEM_PROPS.has(key));
  if (entries.length === 0 && !editable) return null;

  const commit = (next: MemoryFrontmatter) => {
    setEdit(null);
    setAdding(false);
    onChange?.(next);
  };
  const setValue = (key: string, value: MemoryFrontmatterValue) => commit({ ...frontmatter, [key]: value });
  const removeKey = (key: string) => {
    const next = { ...frontmatter };
    delete next[key];
    commit(next);
  };
  const removeItem = (key: string, index: number) => {
    const current = frontmatter[key];
    if (!Array.isArray(current)) {
      removeKey(key);
      return;
    }
    const items = current.filter((_, i) => i !== index);
    if (items.length === 0) removeKey(key);
    else setValue(key, items);
  };

  const renderValue = (key: string, value: MemoryFrontmatterValue, kind: PropKind) => {
    if (edit?.key === key && edit.mode === "edit") {
      return (
        <input
          className="mw-prop-edit-input"
          defaultValue={Array.isArray(value) ? value.join(", ") : String(value)}
          autoFocus
          spellCheck={false}
          onFocus={(e) => e.currentTarget.select()}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              setEdit(null);
              return;
            }
            if (e.key !== "Enter") return;
            const raw = e.currentTarget.value;
            if (raw.trim() === "") removeKey(key);
            else setValue(key, parsePropInput(raw));
          }}
          onBlur={() => setEdit(null)}
        />
      );
    }
    if (kind === "tags" || kind === "list") {
      const items = Array.isArray(value) ? value : [value];
      return (
        <>
          {items.map((item, i) => (
            <span key={i} className="mw-prop-pill">
              {String(item)}
              {editable && (
                <button type="button" className="mw-prop-pill-x" aria-label="Remove" onClick={() => removeItem(key, i)}>
                  ×
                </button>
              )}
            </span>
          ))}
          {editable &&
            (edit?.key === key && edit.mode === "append" ? (
              <input
                className="mw-prop-edit-input"
                placeholder="add…"
                autoFocus
                spellCheck={false}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    e.stopPropagation();
                    setEdit(null);
                    return;
                  }
                  if (e.key !== "Enter") return;
                  const item = e.currentTarget.value.trim();
                  if (item === "") {
                    setEdit(null);
                    return;
                  }
                  const current = frontmatter[key];
                  setValue(key, [...(Array.isArray(current) ? current : [current]), item]);
                }}
                onBlur={() => setEdit(null)}
              />
            ) : (
              <button
                type="button"
                className="mw-prop-pill mw-prop-pill-add"
                title="Add"
                onClick={() => setEdit({ key, mode: "append" })}
              >
                +
              </button>
            ))}
        </>
      );
    }
    if (kind === "checkbox") {
      const className = clsx("mw-prop-check", value && "on");
      return editable ? (
        <button type="button" className={className} aria-pressed={Boolean(value)} onClick={() => setValue(key, !value)} />
      ) : (
        <span className={className} />
      );
    }
    const body = kind === "date" || kind === "number" ? <span className="mono">{String(value)}</span> : String(value);
    return editable ? (
      <button type="button" className="mw-prop-editable" title="Edit" onClick={() => setEdit({ key, mode: "edit" })}>
        {body}
      </button>
    ) : (
      body
    );
  };

  return (
    <section className="mw-props">
      <h2 className="mw-props-head">Properties</h2>
      {entries.map(([key, value]) => {
        const kind = propType(key, value);
        return (
          <div key={key} className="mw-prop-row">
            <PropIcon kind={kind} />
            <span className="mw-prop-key">{key}</span>
            <span className="mw-prop-value">{renderValue(key, value, kind)}</span>
          </div>
        );
      })}
      {editable &&
        (adding ? (
          <div
            className="mw-prop-add-row"
            onBlur={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setAdding(false);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.stopPropagation();
                setAdding(false);
                return;
              }
              if (e.key !== "Enter") return;
              const key = addKeyRef.current?.value.trim() ?? "";
              if (key === "") return;
              setValue(key, parsePropInput(addValueRef.current?.value ?? ""));
            }}
          >
            <PropIcon kind="text" />
            <input ref={addKeyRef} className="key" placeholder="key" autoFocus spellCheck={false} />
            <input ref={addValueRef} className="value" placeholder="value" spellCheck={false} />
          </div>
        ) : (
          <button type="button" className="mw-prop-add" onClick={() => setAdding(true)}>
            + Add property
          </button>
        ))}
    </section>
  );
}
