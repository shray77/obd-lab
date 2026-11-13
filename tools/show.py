#!/usr/bin/env python3
"""Шоу-режим: полная демонстрация проекта одной командой.

    python tools/show.py                 # авто-режим, паузы между шагами
    python tools/show.py --step          # шаг за Enter — удобно показывать живьём
    python tools/show.py --dtc "0171,2187,0420"

Что происходит:
  1. внутри процесса поднимается виртуальная «машина» (ELM327-emulator,
     лицензия CC BY-NC-SA 4.0 — только тестовый стенд, не продукт);
  2. в неё подмешиваются коды неисправностей;
  3. наш диагностический стек подключается как к настоящему адаптеру и
     проводит сеанс с рассказом по шагам;
  4. в конце сохраняется отчёт report_*.md.

Стек и доки: obd_lab/ (протоколы, сканер, вердикты), docs/ (железо,
прецеденты, стенд), README.md.
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = 35000
REPORT_PREFIX = "report_"

STEPS: list[str] = []  # строки будущего отчёта


def say(text: str = "", pause: float = 0.6, step_mode: bool = False) -> None:
    print(text)
    STEPS.append(text)
    if step_mode:
        input("  [Enter — дальше]")
    else:
        time.sleep(pause)


def _silence_emulator_logging() -> None:
    """Прибрать логи эмулятора: он переконфигурирует logging из фонового
    потока (Elm.run -> setup_logging -> basicConfig/dictConfig). Ставим
    ELM_LOG_CFG на несуществующий файл и настраиваем logging первыми:
    тогда его basicConfig становится no-op (обработчик уже есть).
    """
    import logging

    os.environ.setdefault("ELM_LOG_CFG", "нет-такого-конфига.yaml")
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler(sys.stdout))
    root.setLevel(logging.ERROR)


def _clean_raw(text: str) -> str:
    """Сырой ответ ELM -> одна строка без \r, приглашений и эха."""
    cleaned = text.replace("\r", " ")
    cleaned = " ".join(cleaned.split())
    return cleaned.replace("> ", "").replace(">", "").strip()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Шоу-демо OBD-Lab")
    parser.add_argument("--dtc", default="0171,2187,0420", help="коды через запятую (в кавычках!)")
    parser.add_argument("--step", action="store_true", help="ждать Enter на каждом шаге")
    args = parser.parse_args()

    step_mode = args.step

    try:
        from elm.elm import Elm
    except ImportError:
        print("Не установлен тестовый эмулятор. Ставится так:")
        print("  $env:PYTHONUTF8=1")
        print("  python -m pip install ELM327-emulator")
        sys.exit(1)

    import elm.obd_message as obd_message

    codes = []
    for code in args.dtc.split(","):
        code = code.strip()
        if re.fullmatch(r"[0-9A-Fa-f]{4}", code):
            codes.append(code.lower())

    print("=" * 62)
    print("  OBD-Lab — диагностика автомобиля без дилера")
    print("=" * 62)
    say("Как это устроено: к машине подключён адаптер ELM327, он говорит")
    say("по протоколам OBD-II/UDS. Наш код умеет те же самые байты: читает")
    say("коды неисправностей, разбирает их по стандарту ISO и объясняет")
    say("по-русски, что они значат и стоит ли стирать.")
    say("Сейчас мы поднимем виртуальную «машину» и пройдём весь сеанс.")
    say()

    _silence_emulator_logging()
    with Elm(net_port=PORT) as emulator:
        emulator.set_sorted_obd_msg("car")
        for code in codes:
            obd_message.DTC_STORED.append(code)
        say(f"1. Виртуальная машина запущена (сценарий 'car', TCP:{PORT}).")
        if codes:
            say(f"   В её память «записаны» неисправности: {', '.join(codes)}.")
        say("   Заметка: эмулятор — чужой тестовый стенд, в продукт не входит.")
        say()

        from obd_lab.elm327 import Elm327Channel
        from obd_lab.knowledge import KnowledgeBase
        from obd_lab.scanner import ScanTool
        from obd_lab.uds import status_text
        from obd_lab.verdict import verdict

        channel = Elm327Channel(f"socket://127.0.0.1:{PORT}")
        tool = ScanTool(channel)
        kb = KnowledgeBase()

        try:
            say("2. Подключаемся как к реальному адаптеру.")
            protocol = channel.protocol()
            say(f"   Адаптер выбрал протокол: {protocol}.")
            voltage = channel.voltage()
            if voltage is not None:
                say(f"   Напряжение АКБ: {voltage:.1f} В — двигатель работает.")
            say()

            say("3. Спрашиваем у ЭБУ, какие датчики он умеет показывать.")
            raw = channel.request(b"\x01\x00")
            supported = sum(bin(b).count("1") for b in raw[2:6])
            say(f"   Ответ (байтподдержки): {raw[2:6].hex(' ').upper()}")
            say(f"   Вывод: поддерживается {supported} датчиков из первой сотни.")
            say()

            say("4. Читаем VIN — номер кузова, 17 символов.")
            raw_text = _clean_raw(channel._command("0902"))  # noqa: SLF001 — для показа сырых байт
            say(f"   Сырые байты с шины: {raw_text[:64]}…")
            vin = tool.read_vin()
            say(f"   После разбора мультифрейма: VIN = {vin}")
            say("   Виртуальная машина эмулирует три блока и циклит их VIN —")
            say("   это особенность стенда; на реальной машине VIN один.")
            say()

            say("5. Статус: горит ли «чек» и сколько кодов в памяти.")
            status_raw = channel.request(b"\x01\x01")
            status_byte = status_raw[2]
            mil = bool(status_byte & 0x80)
            count = status_byte & 0x7F
            say(f"   Статус-байт: {status_byte:02X} -> чек "
                f"{'ГОРИТ' if mil else 'не горит'}, кодов: {count}")
            say()

            say("6. Читаем коды неисправностей (режим 03).")
            raw03 = _clean_raw(channel._command("03"))  # noqa: SLF001
            say(f"   Сырой ответ: {raw03[:70]}")
            dtcs = tool.read_dtcs()
            say(f"   Разобрано кодов: {len(dtcs)}: {', '.join(d.code for d in dtcs)}")
            say()

            say("7. Главное: что эти коды значат и что с ними делать.")
            for dtc in dtcs:
                say(f"   --- {dtc.code} [{dtc.status:02X}] {status_text(dtc.status)}")
                for line in kb.explain(dtc.code).splitlines():
                    say(f"   {line}")
                for line in verdict(dtc.status, dtc.permanent).render().splitlines():
                    say(f"   {line}")
                say(pause=0.9)

            say("8. Демонстрация стирания (на виртуальной машине — безопасно).")
            try:
                channel._command("04")  # noqa: SLF001 — режим 04, стирание
                again = channel.request(b"\x03")
                say(f"   Отправили 'стереть коды', читаем снова: {again.hex(' ').upper()}")
                say("   Память чиста. На реальной машине так делать нельзя, пока")
                say("   не устранена причина: код вернётся, а нестиранная история")
                say("   помогла бы диагносту. Поэтому у нас стирание — с")
                say("   подтверждением и предупреждениями (live.py --clear).")
            except Exception as exc:  # noqa: BLE001
                say(f"   Стирание на стенде недоступно ({type(exc).__name__}) — пропускаем.")
            say()
            say("9. Готово. Полный отчёт сохранён в файл ниже.")
        finally:
            # Эмулятор не переживает резкий разрыв — закрываемся аккуратно.
            sock = getattr(getattr(channel, "ser", None), "_socket", None)
            if sock is not None:
                try:
                    sock.shutdown(2)  # SHUT_RDWR
                except OSError:
                    pass
            channel.close()

    report = ["# Отчёт диагностики (демо-стенд)", ""]
    report.append(f"Дата: {datetime.now():%d.%m.%Y %H:%M}")
    report.append(f"Стенд: ELM327-emulator, сценарий 'car', подмешанные DTC: {', '.join(codes)}")
    report.append("")
    for line in STEPS:
        if line.strip():
            report.append(line)
        else:
            report.append("")
    report.append("")
    report.append("---")
    report.append("Данные кодов: OBDex (CC0-1.0). Стенд: ELM327-emulator (CC BY-NC-SA 4.0, только тесты).")
    report.append("Код проекта: obd_lab/. Доки: docs/hardware.md, docs/emulator.md, docs/precedents.md")

    name = f"{REPORT_PREFIX}{datetime.now():%Y-%m-%d_%H-%M}.md"
    # utf-8-sig: Windows Блокнот/PowerShell читают без кракозябр.
    Path(name).write_text("\n".join(report), encoding="utf-8-sig")
    print(f"\n>>> Отчёт: {Path(name).resolve()}")


if __name__ == "__main__":
    main()
