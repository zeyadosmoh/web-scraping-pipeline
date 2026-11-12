"""Checkpoint so an interrupted scrape can resume instead of starting over.

The file stores the run id, the next list page to visit, and the detail URLs already
done. Writes are atomic (temp file + os.replace), so a crash mid-write can't corrupt it.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Checkpoint:
    path: Path
    run_id: str = ""
    next_list_url: str | None = None
    done_urls: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Checkpoint":
        path = Path(path)
        if not path.exists():
            return cls(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(path, data.get("run_id", ""), data.get("next_list_url"), set(data.get("done_urls", [])))

    def is_done(self, url: str) -> bool:
        return url in self.done_urls

    def mark_done(self, url: str) -> None:
        self.done_urls.add(url)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"run_id": self.run_id, "next_list_url": self.next_list_url,
                   "done_urls": sorted(self.done_urls)}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".ckpt-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def clear(self) -> None:
        """Called after a run completes successfully."""
        self.path.unlink(missing_ok=True)
        self.run_id, self.next_list_url, self.done_urls = "", None, set()
