# Memory Notebook Editor Design

## Goal

Make Memory feel like a reduced Obsidian notebook: the note is the primary surface, editing stays in place, and provenance recedes until requested.

## Interaction

- Editable notes open as rendered notes. `Cmd/Ctrl+E` switches the same surface between WYSIWYG and Markdown source.
- `Cmd/Ctrl+S` opens the existing diff review. Applying the review remains a memory event with revision checks.
- The title and compact properties remain visually attached to the note. Machine metadata stays in the footer or inspector.
- The rail shows semantic index structure with compact title rows. Placeholder/repeated descriptions are suppressed.
- The inspector is closed by default and uses compact links first; exact evidence and lifecycle details remain available lower down.

## Editor

- Use Milkdown Crepe as the maintained Markdown-first WYSIWYG foundation.
- Keep frontmatter outside the rich editor and reattach it unchanged on save.
- Preserve wikilink Markdown in the source; navigation remains active in read mode.
- Keep a source textarea as the explicit `Cmd/Ctrl+E` alternate, not as a separate full-screen editor.

## Visual direction

- Quiet, flat surfaces; borders only for structure.
- Compact rail rows and subtle selected state.
- Readable note measure around 720px, restrained title scale, no nested metadata cards.
- Contextual editing status/actions instead of a permanent document toolbar.

## Non-goals

- Cloning Obsidian chrome, plugins, graph view, or command system.
- Replacing the server preview/apply/event pipeline.
- Editing raw diagnostics or generated sidecars that the server marks read-only.
