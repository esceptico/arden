from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OutlineSection:
    title: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchOutline:
    sections: tuple[OutlineSection, ...]

    def __post_init__(self) -> None:
        sections = tuple(self.sections)
        titles = [section.title for section in sections]
        if not titles:
            raise ValueError("research outline must include at least one section")
        if any(not title for title in titles):
            raise ValueError("outline section titles must be non-empty")
        if len(set(titles)) != len(titles):
            raise ValueError("outline section titles must be unique")
        object.__setattr__(self, "sections", sections)

    @classmethod
    def from_titles(cls, titles: list[str]) -> "ResearchOutline":
        return cls(tuple(OutlineSection(title=title) for title in titles))

    @property
    def titles(self) -> list[str]:
        return [section.title for section in self.sections]


@dataclass(frozen=True, slots=True)
class CoverageReport:
    coverage: float
    gaps: list[str]
    sections: dict[str, list[str]]

    @property
    def complete(self) -> bool:
        return not self.gaps


def empty_coverage(outline: ResearchOutline) -> dict[str, list[str]]:
    return {section.title: [] for section in outline.sections}


def coverage_report(outline: ResearchOutline, sections: dict[str, list[str]]) -> CoverageReport:
    normalized = {title: list(sections.get(title, [])) for title in outline.titles}
    gaps = [title for title, sources in normalized.items() if not sources]
    return CoverageReport(
        coverage=(len(normalized) - len(gaps)) / len(normalized),
        gaps=gaps,
        sections=normalized,
    )
