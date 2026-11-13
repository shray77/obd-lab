"""Поиск по базе знаний из командной строки.

    python -m obd_lab.cli P0171
    python -m obd_lab.cli P0016 --verdict AF
    python -m obd_lab.cli --stats
"""

from __future__ import annotations

import argparse
import sys

from .knowledge import KnowledgeBase
from .verdict import verdict


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Поиск по базе кодов OBD-II")
    parser.add_argument("code", nargs="?", help="код, например P0171")
    parser.add_argument(
        "--verdict",
        metavar="HEX",
        help="статус-байт в hex (например AF), чтобы сразу получить вердикт",
    )
    parser.add_argument("--stats", action="store_true", help="сколько кодов доступно")
    args = parser.parse_args()

    kb = KnowledgeBase()

    if args.stats:
        print(f"Русская курируемая база: {len(kb.codes)} кодов")
        if kb.reference.available:
            print(f"Справочник OBDex: {kb.reference.count()} кодов")
        else:
            print("Справочник OBDex не подключён (tools/import_obdex.py)")
        return

    if not args.code:
        parser.error("нужен код или --stats")

    print(kb.explain(args.code))
    if args.verdict:
        raw = int(args.verdict, 16)
        print()
        print(verdict(raw).render())


if __name__ == "__main__":
    main()
