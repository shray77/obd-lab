#!/usr/bin/env python3
"""Собрать локальный справочник OBDex (CC0) в SQLite.

    python tools/import_obdex.py                  # скачать и собрать
    python tools/import_obdex.py --source FILE    # из локального JSON

OBDex: https://github.com/foerbsnavi/obdex
Данные распространяются под CC0-1.0: атрибуция не обязательна,
но в README она указана.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.request
from pathlib import Path

URL = "https://foerbsnavi.github.io/OBDex/generic.min.json"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "obdex.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS obdex (
    code           TEXT PRIMARY KEY,
    title_en       TEXT,
    title_de       TEXT,
    description_en TEXT,
    causes_json    TEXT,
    symptoms_json  TEXT,
    flags_json     TEXT,
    difficulty     TEXT,
    diy_possible   INTEGER,
    cost_min       REAL,
    cost_max       REAL,
    hours_min      REAL,
    hours_max      REAL,
    reviewed       TEXT
);
"""


def load(source: Path | None) -> list[dict]:
    if source is not None:
        return json.loads(source.read_text(encoding="utf-8"))
    with urllib.request.urlopen(URL) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def build(entries: list[dict], out: Path) -> None:
    tmp = out.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM obdex")

    rows = []
    for entry in entries:
        causes = [
            {
                "likelihood": cause.get("likelihood"),
                "en": (cause.get("label") or {}).get("en"),
            }
            for cause in entry.get("common_causes") or []
        ]
        symptoms = [item.get("en") for item in entry.get("symptoms") or [] if item.get("en")]
        repair = entry.get("repair") or {}
        cost = repair.get("estimated_cost_eur") or [None, None]
        hours = repair.get("estimated_hours") or [None, None]
        rows.append(
            (
                entry["code"],
                (entry.get("title") or {}).get("en"),
                (entry.get("title") or {}).get("de"),
                (entry.get("description") or {}).get("en"),
                json.dumps(causes, ensure_ascii=False),
                json.dumps(symptoms, ensure_ascii=False),
                json.dumps(entry.get("flags") or {}, ensure_ascii=False),
                repair.get("difficulty"),
                int(bool(repair.get("diy_possible"))),
                cost[0],
                cost[1],
                hours[0],
                hours[1],
                entry.get("reviewed"),
            )
        )

    conn.executemany(
        "INSERT OR REPLACE INTO obdex VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()
    tmp.replace(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="OBDex -> SQLite")
    parser.add_argument("--source", type=Path, help="локальный generic.min.json")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    entries = load(args.source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    build(entries, args.out)
    size = args.out.stat().st_size / 1e6
    print(f"OK: {len(entries)} кодов -> {args.out} ({size:.1f} МБ)")


if __name__ == "__main__":
    main()
