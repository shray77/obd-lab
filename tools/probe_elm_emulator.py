#!/usr/bin/env python3
"""Пробник: Elm327Channel против ELM327-emulator в одном соединении.

Тестовый стенд (CC BY-NC-SA 4.0), не входит в продукт.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from obd_lab.elm327 import Elm327Channel
from obd_lab.scanner import ScanTool

PORT = sys.argv[1] if len(sys.argv) > 1 else "socket://127.0.0.1:35000"


def main() -> None:
    ch = Elm327Channel(PORT)
    try:
        print("protocol():", repr(ch.protocol()), "| count_byte:", ch.count_byte)
        print("voltage():", ch.voltage())
        print("raw 0902:", repr(ch._command("0902")[:500]))  # noqa: SLF001
        print("raw 03:  ", repr(ch._command("03")[:300]))  # noqa: SLF001

        for label, payload in [
            ("0100", b"\x01\x00"),
            ("0101", b"\x01\x01"),
            ("03", b"\x03"),
            ("07", b"\x07"),
            ("0A", b"\x0A"),
            ("0902", b"\x09\x02"),
            ("19 02 FF", b"\x19\x02\xFF"),
            ("22 F1 90", b"\x22\xF1\x90"),
        ]:
            try:
                response = ch.request(payload)
                print(f"{label:>9} -> {response.hex(' ').upper()}")
            except Exception as exc:  # noqa: BLE001
                print(f"{label:>9} -> {type(exc).__name__}: {exc}")

        tool = ScanTool(ch)
        try:
            print("\nVIN:", tool.read_vin())
        except Exception as exc:  # noqa: BLE001
            print("VIN ошибка:", exc)

        try:
            print("Коды (read_dtcs):")
            for dtc in tool.read_dtcs():
                print(f"  {dtc.code} [{dtc.status:02X}]")
        except Exception as exc:  # noqa: BLE001
            print("  ошибка чтения кодов:", exc)

        try:
            snap = tool.snapshot()
            print(
                f"Snapshot: VIN={snap.vin}, MIL={snap.mil_on}, "
                f"кодов={snap.dtc_count}"
            )
        except Exception as exc:  # noqa: BLE001
            print("Snapshot ошибка:", exc)
    finally:
        # Аккуратный разрыв: эмулятор плохо переживает резкий close.
        try:
            ch.ser._socket.shutdown(2)  # noqa: SLF001 — SHUT_RDWR
        except Exception:  # noqa: BLE001
            pass
        ch.close()


if __name__ == "__main__":
    main()
