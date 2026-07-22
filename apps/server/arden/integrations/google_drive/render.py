def _paragraph_text(paragraph: dict) -> str:
    return "".join(str(element.get("textRun", {}).get("content", "")) for element in paragraph.get("elements", []))


def _structural_text(elements: list[dict]) -> str:
    chunks: list[str] = []
    for element in elements:
        if paragraph := element.get("paragraph"):
            chunks.append(_paragraph_text(paragraph))
        elif table := element.get("table"):
            rows: list[str] = []
            for row in table.get("tableRows", []):
                cells = [
                    _structural_text(cell.get("content", [])).strip().replace("\n", " ")
                    for cell in row.get("tableCells", [])
                ]
                rows.append("\t".join(cells))
            chunks.append("\n".join(rows))
        elif toc := element.get("tableOfContents"):
            chunks.append(_structural_text(toc.get("content", [])))
    return "".join(chunks)


def flatten_google_doc(payload: dict) -> str:
    tabs = payload.get("tabs") or []
    if tabs:
        parts: list[str] = []
        for tab in tabs:
            document_tab = tab.get("documentTab", {})
            text = _structural_text(document_tab.get("body", {}).get("content", [])).strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts)
    return _structural_text(payload.get("body", {}).get("content", [])).strip()
