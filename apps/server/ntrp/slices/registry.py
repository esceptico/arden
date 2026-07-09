import json
from dataclasses import asdict
from pathlib import Path

from ntrp.slices.models import Autonomy, Slice


class SliceRegistry:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> list[Slice]:
        if not self._path.exists():
            return []
        data = json.loads(self._path.read_text())
        # Tolerate legacy fields (e.g. `related`, dropped from the projection);
        # this whole file is deleted once the boot migration folds slices.json
        # into the projects table.
        return [
            Slice(key=s["key"], title=s["title"], page_path=s.get("page_path"), autonomy=s.get("autonomy"))
            for s in data["slices"]
        ]

    def save(self, slices: list[Slice]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"slices": [asdict(s) for s in slices]}, indent=2))

    def get(self, key: str) -> Slice:
        slices = self.load()
        for s in slices:
            if s.key == key:
                return s
        raise KeyError(f"unknown slice '{key}'; valid: {[s.key for s in slices]}")

    def update_autonomy(self, key: str, autonomy: Autonomy) -> Slice:
        slices = self.load()
        for s in slices:
            if s.key == key:
                s.autonomy = autonomy
                self.save(slices)
                return s
        raise KeyError(f"unknown slice '{key}'; valid: {[s.key for s in slices]}")

    def create(self, key: str, title: str, page_path: str) -> Slice:
        slices = self.load()
        if any(s.key == key for s in slices):
            raise ValueError(f"slice '{key}' already exists")
        slice_ = Slice(key=key, title=title, page_path=page_path, autonomy="observe")
        slices.append(slice_)
        self.save(slices)
        return slice_
