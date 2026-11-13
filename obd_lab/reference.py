"""Справочный слой: локальная копия OBDex (CC0).

OBDex покрывает 9 533 общих кода SAE J2012 с причинами, симптомами,
стоимостью и источниками. Это не наша база: это внешний справочник,
который подключается, когда в русской курируемой базе кода нет.

Данные: https://github.com/foerbsnavi/obdex (CC0-1.0).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "obdex.sqlite3"


@dataclass
class ReferenceEntry:
    code: str
    title: str
    description: str
    causes: list[dict] = field(default_factory=list)
    symptoms: list[str] = field(default_factory=list)
    flags: dict = field(default_factory=dict)
    difficulty: str = ""
    cost: tuple[float, float] | None = None


class ReferenceBase:
    """Только чтение. Если файла нет — справочник просто недоступен."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self._conn: sqlite3.Connection | None = None
        self._count: int | None = None

    @property
    def available(self) -> bool:
        return self.path.exists()

    def _connect(self) -> sqlite3.Connection | None:
        if not self.available:
            return None
        if self._conn is None:
            self._conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def count(self) -> int:
        if self._count is None:
            conn = self._connect()
            if conn is None:
                return 0
            self._count = int(conn.execute("SELECT COUNT(*) FROM obdex").fetchone()[0])
        return self._count

    def get(self, code: str) -> ReferenceEntry | None:
        conn = self._connect()
        if conn is None:
            return None
        row = conn.execute(
            "SELECT * FROM obdex WHERE code = ?", (code.upper(),)
        ).fetchone()
        if row is None:
            return None
        cost = None
        if row["cost_min"] is not None and row["cost_max"] is not None:
            cost = (row["cost_min"], row["cost_max"])
        return ReferenceEntry(
            code=row["code"],
            title=row["title_en"] or "",
            description=row["description_en"] or "",
            causes=json.loads(row["causes_json"] or "[]"),
            symptoms=json.loads(row["symptoms_json"] or "[]"),
            flags=json.loads(row["flags_json"] or "{}"),
            difficulty=row["difficulty"] or "",
            cost=cost,
        )
