def format_lines_with_pagination(
    content: str,
    offset: int = 1,
    limit: int = 500,
) -> str:
    lines = content.split("\n")
    total_lines = len(lines)

    offset = max(1, min(offset, total_lines))
    start_idx = offset - 1
    end_idx = min(start_idx + limit, total_lines)

    selected_lines = lines[start_idx:end_idx]

    output_lines = []
    for i, line in enumerate(selected_lines):
        line_num = start_idx + i + 1
        output_lines.append(f"{line_num:>6}|{line}")

    header = f"[{total_lines} lines]"
    if start_idx > 0 or end_idx < total_lines:
        header = f"[{total_lines} lines, showing {offset}-{end_idx}]"

    return header + "\n" + "\n".join(output_lines)
