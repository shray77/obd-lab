#!/usr/bin/env python3
"""Поднять ELM327-emulator на TCP 127.0.0.1:35000 для проверки Elm327Channel.

    python tools/run_elm_emulator.py

Пакет: pip install ELM327-emulator
Лицензия эмулятора — CC BY-NC-SA 4.0: годится только как тестовый стенд,
в наш продукт и в requirements.txt не включается.
"""

from __future__ import annotations

import sys
import time

from elm.elm import Elm


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 35000
    scenario = sys.argv[2] if len(sys.argv) > 2 else "car"
    with Elm(net_port=port) as emulator:
        emulator.set_sorted_obd_msg(scenario)
        if len(sys.argv) > 3:
            # Подмешать DTC в эмулированный блок: argv[3:] = "0171,2187".
            # Коды обязаны быть ровно 4 hex-символа: эмулятор молча выбрасывает
            # элементы с нечётной длиной, и мы получим "тихо пропавший" код.
            import re

            import elm.obd_message as obd_message

            for code in ",".join(sys.argv[3:]).split(","):
                if not re.fullmatch(r"[0-9A-Fa-f]{4}", code.strip()):
                    raise SystemExit(
                        f"Некорректный DTC {code!r}: нужно 4 hex-символа, "
                        f"напр. 0171. Осторожно: PowerShell без кавычек "
                        f"срезает ведущие нули — аргумент брать в кавычки."
                    )
                obd_message.DTC_STORED.append(code.strip().lower())
            print(f"Подмешаны DTC: {obd_message.DTC_STORED}", flush=True)
        print(f"ELM327-emulator: TCP 127.0.0.1:{port}, сценарий {scenario!r}.", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
