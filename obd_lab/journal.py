"""Журнал: история статусов DTC по циклам зажигания.

Именно журнал, а не разовое чтение, находит плавающие неисправности.
Один снимок говорит "что сейчас", история говорит "при каких условиях".
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .uds import BIT, Dtc

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle       INTEGER NOT NULL,
    code        TEXT    NOT NULL,
    status      INTEGER NOT NULL,
    permanent   INTEGER NOT NULL DEFAULT 0,
    ambient_c   REAL,
    battery_v   REAL,
    coolant_c   REAL,
    rpm         REAL,
    speed       REAL
);
CREATE TABLE IF NOT EXISTS clears (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle   INTEGER NOT NULL,
    removed TEXT,
    kept    TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_code ON snapshots(code);
"""


@dataclass
class CodeReport:
    code: str
    occurrences: int
    cycles: list[int]
    permanent: bool
    last_status: int
    note: str | None = None


class Journal:
    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def record_cycle(
        self,
        cycle: int,
        conditions: dict[str, float],
        dtcs: list[Dtc],
        permanent: set[str] | None = None,
    ) -> None:
        permanent = permanent or set()
        for dtc in dtcs:
            if dtc.status == 0:
                continue
            self.conn.execute(
                """
                INSERT INTO snapshots
                    (cycle, code, status, permanent, ambient_c, battery_v, coolant_c, rpm, speed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle,
                    dtc.code,
                    dtc.status,
                    int(dtc.code in permanent or dtc.permanent),
                    conditions.get("ambient_c"),
                    conditions.get("battery_v"),
                    conditions.get("coolant_c"),
                    conditions.get("rpm"),
                    conditions.get("speed"),
                ),
            )
        self.conn.commit()

    def log_clear(self, cycle: int, removed: list[str], kept: list[str]) -> None:
        self.conn.execute(
            "INSERT INTO clears (cycle, removed, kept) VALUES (?, ?, ?)",
            (cycle, ",".join(removed), ",".join(kept)),
        )
        self.conn.commit()

    def rows_for(self, code: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM snapshots WHERE code = ? ORDER BY cycle", (code,)
            )
        )

    def failures(self, code: str) -> list[sqlite3.Row]:
        return [
            row
            for row in self.rows_for(code)
            if row["status"] & (BIT["testFailed"] | BIT["testFailedThisOperationCycle"])
        ]

    def codes(self) -> list[str]:
        return [
            row["code"]
            for row in self.conn.execute(
                "SELECT code, MAX(cycle) AS last FROM snapshots GROUP BY code ORDER BY last DESC"
            )
        ]

    # ------------------------------------------------------------------
    # Аналитика
    # ------------------------------------------------------------------
    def intermittency_note(self, code: str) -> str | None:
        """Найти закономерность в плавающих сбоях."""
        failures = self.failures(code)
        if len(failures) < 2:
            return None

        ambients = [row["ambient_c"] for row in failures if row["ambient_c"] is not None]
        if ambients and all(value < 0 for value in ambients):
            return (
                f"все {len(ambients)} сбоев при отрицательной температуре "
                f"({min(ambients):+.0f}…{max(ambients):+.0f} °C) — искать "
                "холодный пуск, подсос, конденсат"
            )

        batteries = [row["battery_v"] for row in failures if row["battery_v"] is not None]
        if batteries and all(value < 12.0 for value in batteries):
            return (
                f"все сбои при напряжении ниже 12 В ({min(batteries):.1f}…"
                f"{max(batteries):.1f} В) — вероятно, просадка АКБ при пуске"
            )

        coolants = [row["coolant_c"] for row in failures if row["coolant_c"] is not None]
        if coolants and all(value < 40 for value in coolants):
            return "все сбои на непрогретом двигателе — проверять цепи на холодную"

        cycles = [row["cycle"] for row in failures]
        return f"сбои в циклах {cycles} — явной корреляции по условиям не видно"

    def report(self) -> list[CodeReport]:
        out: list[CodeReport] = []
        for code in self.codes():
            rows = self.rows_for(code)
            last = rows[-1]
            out.append(
                CodeReport(
                    code=code,
                    occurrences=len(self.failures(code)),
                    cycles=[row["cycle"] for row in self.failures(code)],
                    permanent=bool(last["permanent"]),
                    last_status=last["status"],
                    note=self.intermittency_note(code),
                )
            )
        return out
