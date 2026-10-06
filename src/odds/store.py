"""Append-only odds store (ADR 0030): artifacts/odds/<fixture_id>/{quotes.jsonl,meta.json}.
Quotes are de-duplicated by their content-derived `quote_id`; nothing is ever rewritten."""

import json
from pathlib import Path

from .quotes import OddsQuote


class OddsStore:
    def __init__(self, root: Path, fixture_id: str):
        self.dir = Path(root) / "artifacts" / "odds" / fixture_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.quotes_path = self.dir / "quotes.jsonl"
        self.meta_path = self.dir / "meta.json"

    def write_meta(self, meta: dict) -> None:
        if not self.meta_path.exists():  # identity of the fixture never changes once recorded
            self.meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    def meta(self) -> dict:
        return json.loads(self.meta_path.read_text(encoding="utf-8"))

    def quotes(self) -> list[OddsQuote]:
        if not self.quotes_path.exists():
            return []
        out = []
        for line in self.quotes_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                d.pop("quote_id", None)
                out.append(OddsQuote.model_validate(d))
        return out

    def add(self, quotes: list[OddsQuote]) -> int:
        known = {q.quote_id for q in self.quotes()}
        n = 0
        with self.quotes_path.open("a", encoding="utf-8") as f:
            for q in quotes:
                if q.quote_id not in known:
                    f.write(q.to_json_line() + "\n")
                    known.add(q.quote_id)
                    n += 1
        return n
