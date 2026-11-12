"""Checkpoint so an interrupted full scrape can resume instead of starting over.

Files (in one state directory per source):
  checkpoint.json   run id, parameters, next list page, detail URLs done, failures
  raw_rows.jsonl    every parsed row so far, so a resumed run still has the
                    complete catalogue (and can safely detect removals)
The JSON is written atomically (temp file + os.replace). The JSONL is append-only.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scraper.books import RawBook


@dataclass
class Checkpoint:
    path: Path | None  # None = in-memory only (used for --limit runs)
    run_id: str = ""
    params: dict = field(default_factory=dict)
    next_list_url: str | None = None
    done_urls: set[str] = field(default_factory=set)
    failures: list[dict] = field(default_factory=list)

    @property
    def staging_path(self) -> Path | None:
        return self.path.with_name("raw_rows.jsonl") if self.path else None

    @classmethod
    def load(cls, path: str | os.PathLike | None) -> "Checkpoint":
        if path is None:
            return cls(None)
        path = Path(path)
        if not path.exists():
            return cls(path)
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(path, d.get("run_id", ""), d.get("params", {}), d.get("next_list_url"),
                   set(d.get("done_urls", [])), d.get("failures", []))

    @property
    def exists(self) -> bool:
        return bool(self.run_id)

    def is_done(self, url: str) -> bool:
        return url in self.done_urls

    def mark_done(self, url: str) -> None:
        self.done_urls.add(url)

    def stage(self, raw: RawBook) -> None:
        if self.staging_path:
            self.staging_path.parent.mkdir(parents=True, exist_ok=True)
            with self.staging_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(raw), ensure_ascii=False) + "\n")

    def staged_rows(self) -> list[RawBook]:
        p = self.staging_path
        if not p or not p.exists():
            return []
        rows, seen = [], set()
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                raw = RawBook(**json.loads(line))
                if raw.url in self.done_urls and raw.url not in seen:  # ignore a torn last write
                    rows.append(raw); seen.add(raw.url)
        return rows

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"run_id": self.run_id, "params": self.params, "next_list_url": self.next_list_url,
                   "done_urls": sorted(self.done_urls), "failures": self.failures}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".ckpt-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def clear(self) -> None:
        """Called after a run is stored successfully."""
        if self.path is not None:
            self.path.unlink(missing_ok=True)
            self.staging_path.unlink(missing_ok=True)
        self.run_id, self.params, self.next_list_url = "", {}, None
        self.done_urls, self.failures = set(), []
