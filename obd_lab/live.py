"""Живое подключение к машине через реальный адаптер.

    python -m obd_lab.live --port COM5
    python -m obd_lab.live --port COM5 --clear
    python -m obd_lab.live --port socket://127.0.0.1:35000

Пока это тонкая обёртка: VIN, напряжение, коды, вердикты, опционально
стирание с подтверждением. Журнал и база знаний подключаются позже.
"""

from __future__ import annotations

import argparse
import sys

from .elm327 import Elm327Channel
from .scanner import ScanTool
from .uds import status_text
from .verdict import verdict


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Живая диагностика через ELM327")
    parser.add_argument("--port", required=True, help="COM5 или socket://host:port")
    parser.add_argument("--baud", type=int, default=38400)
    parser.add_argument("--clear", action="store_true", help="стереть коды (с подтверждением)")
    args = parser.parse_args()

    channel = Elm327Channel(args.port, baudrate=args.baud)
    tool = ScanTool(channel)

    try:
        print(f"Канал: {channel.name}")
        try:
            print(f"Протокол: {channel.protocol()}")
            voltage = channel.voltage()
            if voltage is not None:
                print(f"Напряжение: {voltage:.1f} В")
                if voltage < 11.5:
                    print(
                        "ВНИМАНИЕ: напряжение ниже 11,5 В — типичная причина пачки "
                        "ошибок на ровном месте (P0562 и свита). Сначала АКБ."
                    )
            print(f"VIN: {tool.read_vin()}")
        except Exception as exc:  # noqa: BLE001
            print(f"Идентификация недоступна: {exc}")

        try:
            dtcs = tool.read_dtcs()
        except Exception as exc:  # noqa: BLE001
            print(f"Коды прочитать не удалось: {exc}")
            return

        if not dtcs:
            print("Кодов в памяти нет.")
        for dtc in dtcs:
            print()
            print(f"{dtc.code} [{dtc.status:02X}] {status_text(dtc.status)}")
            print(verdict(dtc.status, dtc.permanent).render())

        if args.clear:
            dangerous = [d.code for d in dtcs if verdict(d.status, d.permanent).level == "do_not_clear"]
            if dangerous:
                print()
                print(f"ВНИМАНИЕ: активные неисправности: {', '.join(dangerous)}")
                print("Они вернутся сразу после стирания. Причина не устранена.")
            answer = input("Стереть коды? [y/N] ").strip().lower()
            if answer == "y":
                tool.clear()
                print("Команда стирания отправлена.")
    finally:
        channel.close()


if __name__ == "__main__":
    main()
