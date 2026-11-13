"""Демонстрация obd-lab на виртуальной машине.

Сценарий: 12 циклов зажигания, среди них холодные пуски, слабый АКБ,
одна разовая помеха и одна заменённая деталь. Дальше — журнал, вердикты,
стирание и проверка, что вернулось.
"""

from __future__ import annotations

import sys

from .canbus import VirtualBus
from .channel import VirtualChannel
from .ecu import Monitor, VirtualEcu
from .journal import Journal
from .knowledge import KnowledgeBase
from .scanner import ScanTool
from .uds import Dtc
from .verdict import LEVEL_LABELS, verdict

LINE = "=" * 74
THIN = "-" * 74

# цикл: (температура воздуха, напряжение, холодный пуск, скорость, что происходит)
SCENARIO: list[dict] = [
    {"ambient_c": 20, "battery_v": 12.6, "cold_start": False, "speed": 60},
    {"ambient_c": 18, "battery_v": 12.6, "cold_start": False, "speed": 55},
    {"ambient_c": 15, "battery_v": 12.5, "cold_start": False, "speed": 40},
    {"ambient_c": -8, "battery_v": 12.4, "cold_start": True, "speed": 0},
    {"ambient_c": -10, "battery_v": 12.4, "cold_start": True, "speed": 0},
    {"ambient_c": -6, "battery_v": 11.1, "cold_start": True, "speed": 0},
    {"ambient_c": 5, "battery_v": 12.5, "cold_start": False, "speed": 70},
    {"ambient_c": 12, "battery_v": 12.5, "cold_start": False, "speed": 50},
    {"ambient_c": -9, "battery_v": 12.4, "cold_start": True, "speed": 0},
    {"ambient_c": 3, "battery_v": 11.8, "cold_start": True, "speed": 10},
    {"ambient_c": 15, "battery_v": 12.5, "cold_start": False, "speed": 65},
    {"ambient_c": -7, "battery_v": 12.4, "cold_start": True, "speed": 0},
]


def build_vehicle() -> tuple[VirtualEcu, ScanTool]:
    bus = VirtualBus()
    monitors = [
        Monitor("P0171", "Бедная смесь, банк 1", lambda c: c["ambient_c"] < -5),
        Monitor(
            "P2187",
            "Бедная смесь на холостом ходу",
            lambda c: c["ambient_c"] < -5 and c["cold_start"] >= 1,
        ),
        Monitor("P0420", "Эффективность катализатора", lambda c: c["cycle"] == 2),
        Monitor("P0455", "Утечка EVAP", lambda c: c["cycle"] == 3, permanent=True),
        Monitor("P0562", "Низкое напряжение сети", lambda c: c["battery_v"] < 11.5),
        Monitor("U0100", "Потеря связи с ЭБУ", lambda c: c["cycle"] == 5),
    ]
    ecu = VirtualEcu(bus, monitors=monitors)
    tool = ScanTool(VirtualChannel(bus))
    return ecu, tool


def run_cycle(ecu: VirtualEcu, tool: ScanTool, journal: Journal, spec: dict) -> None:
    ecu.advance_cycle(
        ambient_c=spec["ambient_c"],
        battery_v=spec["battery_v"],
        cold_start=spec["cold_start"],
        speed=spec["speed"],
        rpm=820 if spec["speed"] == 0 else 2100,
    )
    dtcs = tool.read_dtcs()
    journal.record_cycle(ecu.cycle, ecu.conditions, dtcs)


def short_flags(dtc: Dtc) -> str:
    flags = dtc.flags
    parts: list[str] = []
    if flags["testFailed"]:
        parts.append("активна")
    elif flags["testFailedThisOperationCycle"]:
        parts.append("сбой в цикле")
    if flags["confirmedDTC"]:
        parts.append("подтверждена")
    if flags["pendingDTC"]:
        parts.append("pending")
    if flags["testFailedSinceLastClear"] and not flags["testFailed"]:
        parts.append("историческая")
    if flags["warningIndicatorRequested"]:
        parts.append("просит чек")
    return ", ".join(parts) or "—"


def print_snapshot(tool: ScanTool) -> None:
    snap = tool.snapshot()
    print(f"VIN: {snap.vin}")
    print(f"Лампа: {'ГОРИТ' if snap.mil_on else 'погашена'}   подтверждённых кодов: {snap.dtc_count}")
    if not snap.dtcs:
        print("  (ошибок в памяти нет)")
        return
    print(f"  {'Код':<6} {'Статус':<7} {'Признаки':<44} Вердикт")
    for dtc in sorted(snap.dtcs, key=lambda item: item.code):
        mark = " *" if dtc.permanent else ""
        print(
            f"  {dtc.code + mark:<6} {dtc.status:02X}      "
            f"{short_flags(dtc):<44} {verdict(dtc.status, dtc.permanent).label}"
        )
    print("  * — permanent DTC: сканером не стирается")


def print_journal(journal: Journal) -> None:
    print(f"  {'Код':<6} {'сбоев':>6}  {'циклы':<16} наблюдение")
    for item in journal.report():
        cycles = ",".join(str(cycle) for cycle in item.cycles) or "—"
        note = item.note or "—"
        tag = " [permanent]" if item.permanent else ""
        print(f"  {item.code:<6} {item.occurrences:>6}  {cycles:<16} {note}{tag}")


def show_verdicts(dtcs: list[Dtc]) -> None:
    for dtc in sorted(dtcs, key=lambda item: item.code):
        print(f"\n{dtc.code}:")
        print(verdict(dtc.status, dtc.permanent).render())


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    kb = KnowledgeBase()
    journal = Journal()
    ecu, tool = build_vehicle()

    print(LINE)
    print("OBD-LAB  ·  виртуальная машина, 12 циклов зажигания")
    print(LINE)
    for spec in SCENARIO:
        run_cycle(ecu, tool, journal, spec)
    print(f"Отработано циклов: {ecu.cycle}")

    print("\n" + LINE)
    print("1. ЧТО ВИДИТ СКАНЕР СЕЙЧАС")
    print(LINE)
    print_snapshot(tool)

    print("\n" + LINE)
    print("2. ЖУРНАЛ: ЧТО БЫЛО И ПРИ КАКИХ УСЛОВИЯХ")
    print(LINE)
    print_journal(journal)

    print("\n" + LINE)
    print("3. РАЗБОР ПЛАВАЮЩЕЙ ОШИБКИ P0171")
    print(LINE)
    try:
        freeze = tool.read_freeze("P0171")
        print(
            f"Freeze frame: {freeze['ambient_c']:+.0f} °C, {freeze['rpm']:.0f} об/мин, "
            f"{freeze['battery_v']:.1f} В, ОЖ {freeze['coolant_c']:+.0f} °C"
        )
    except Exception:  # noqa: BLE001
        print("Freeze frame недоступен")
    print()
    print(kb.explain("P0171"))
    print()
    print(verdict(next(d.status for d in tool.read_dtcs() if d.code == "P0171")).render())

    print("\n" + LINE)
    print("4. ЧТО ИНСТРУМЕНТ ГОВОРИТ ОБ ОСТАЛЬНЫХ")
    print(LINE)
    show_verdicts(tool.read_dtcs())

    print("\n" + LINE)
    print("5. СТИРАНИЕ: ДЕЛАЕМ ТО, ЧТО ПРОСИЛ ПОЛЬЗОВАТЕЛЬ")
    print(LINE)
    before = tool.read_dtcs()
    keep = [d.code for d in before if verdict(d.status, d.permanent).level == "do_not_clear"]
    blocked = [d.code for d in before if d.permanent]
    if keep:
        print(f"Предупреждение: не следовало бы стирать {', '.join(keep)}")
    if blocked:
        print(f"Permanent, всё равно останутся: {', '.join(blocked)}")
    tool.clear()
    after = tool.read_dtcs()
    removed = sorted({d.code for d in before} - {d.code for d in after})
    kept = sorted({d.code for d in after})
    print(f"Стёрто: {', '.join(removed) or 'ничего'}")
    print(f"Осталось: {', '.join(kept) or 'ничего'}")
    journal.log_clear(ecu.cycle, removed, kept)

    print("\n" + LINE)
    print("6. ЕЩЁ ТРИ ЦИКЛА: ВЕРНУЛОСЬ ИЛИ НЕТ")
    print(LINE)
    for spec in [
        {"ambient_c": -9, "battery_v": 12.4, "cold_start": True, "speed": 0},
        {"ambient_c": -10, "battery_v": 12.4, "cold_start": True, "speed": 0},
        {"ambient_c": 8, "battery_v": 12.5, "cold_start": False, "speed": 60},
    ]:
        run_cycle(ecu, tool, journal, spec)
    print_snapshot(tool)

    print("\n" + LINE)
    print("7. ВЫВОД")
    print(LINE)
    returned = [d.code for d in tool.read_dtcs() if d.code in {"P0171", "P2187"}]
    if returned:
        print(
            f"{', '.join(returned)} вернулись после стирания на первом же холодном пуске. "
            "Причина не устранена."
        )
    print("P0455 осталась, потому что permanent: её нельзя стереть сканером.")
    print(
        "Стирание убрало только исторические записи — это единственный случай, "
        "когда оно осмысленно."
    )

    journal.close()


if __name__ == "__main__":
    main()
