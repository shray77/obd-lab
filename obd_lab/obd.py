"""OBD-II (SAE J1979): коды неисправностей и PID-ы live-данных.

Здесь только чистые функции кодирования/декодирования — никакой шины.
Именно поэтому их легко проверить тестами и переиспользовать с любым
транспортом.
"""

from __future__ import annotations

from dataclasses import dataclass

LETTERS = "PCBU"

PID_NAMES: dict[int, tuple[str, str]] = {
    0x04: ("Нагрузка двигателя", "%"),
    0x05: ("Температура охлаждающей жидкости", "°C"),
    0x06: ("Краткосрочная коррекция, банк 1", "%"),
    0x07: ("Долгосрочная коррекция, банк 1", "%"),
    0x08: ("Краткосрочная коррекция, банк 2", "%"),
    0x09: ("Долгосрочная коррекция, банк 2", "%"),
    0x0C: ("Обороты двигателя", "об/мин"),
    0x0D: ("Скорость", "км/ч"),
    0x0F: ("Температура воздуха на впуске", "°C"),
    0x10: ("Расход воздуха (MAF)", "г/с"),
    0x11: ("Положение дросселя", "%"),
    0x1F: ("Время с запуска двигателя", "с"),
    0x21: ("Пробег с включённой лампой", "км"),
    0x2F: ("Уровень топлива", "%"),
    0x33: ("Атмосферное давление", "кПа"),
    0x42: ("Напряжение бортовой сети", "В"),
    0x46: ("Температура окружающего воздуха", "°C"),
}

SUPPORTED_01_20 = (0x01, 0x04, 0x05, 0x06, 0x07, 0x0C, 0x0D, 0x0F, 0x10, 0x11, 0x1F, 0x21, 0x2F, 0x33, 0x42, 0x46)


def supported_pids_mask(pids: tuple[int, ...] | list[int]) -> bytes:
    """Битовая карта поддерживаемых PID-ов для запроса 01 00."""
    value = 0
    for pid in pids:
        if 1 <= pid <= 0x20:
            value |= 1 << (32 - pid)
    return value.to_bytes(4, "big")


def decode_dtc(hi: int, lo: int) -> str:
    """Два байта -> строка вида P0171."""
    return (
        f"{LETTERS[(hi >> 6) & 0x03]}"
        f"{(hi >> 4) & 0x03}"
        f"{hi & 0x0F:X}"
        f"{lo >> 4:X}"
        f"{lo & 0x0F:X}"
    )


def encode_dtc(code: str) -> bytes:
    """Строка вида P0171 -> два байта."""
    code = code.strip().upper()
    if len(code) != 5 or code[0] not in LETTERS:
        raise ValueError(f"не похоже на OBD-II код: {code!r}")
    index = LETTERS.index(code[0])
    d1, d2, d3, d4 = (int(ch, 16) for ch in code[1:])
    return bytes([(index << 6) | ((d1 & 0x03) << 4) | d2, (d3 << 4) | d4])


def decode_dtc_list(data: bytes) -> list[str]:
    """Поток байт по два -> список кодов."""
    return [decode_dtc(data[i], data[i + 1]) for i in range(0, len(data) - 1, 2)]


def decode_dtc_block(data: bytes, count_first: bool | None = None) -> list[str]:
    """Ответ 43/47/4A без SID -> список кодов.

    Форматы отличаются транспортами:
      * CAN (ISO 15765-4):  <число DTC> <код> <код> ... [добивка 00];
      * K-line (ISO 9141-2, KWP): <код> <код> ... без счётчика.

    count_first=None включает эвристику: считаем первый байт счётчиком,
    если он укладывается в длину и хвост после кодов пуст или нулевой.
    Явное значение полезно, когда протокол известен (ATDPN у ELM327).

    Даже при count_first=True делаем sanity-check: в счётчиковом формате
    после счётчика идут пары байт (+ нулевая добивка), т.е. длина блока
    нечётная. Часть ЭБУ (наш тестовый эмулятор, некоторые блоки) шлёт
    блок без счётчика и на CAN — тогда первый код легко перепутать со
    счётчиком: "43 01 71 21 87 04 20" — это P0171/P2187/P0420, а не
    один C3121.
    """
    if not data:
        return []
    if count_first is None:
        count = data[0]
        fits = 0 < count and count * 2 <= len(data) - 1
        clean_tail = all(byte == 0 for byte in data[1 + count * 2 :])
        count_first = fits and clean_tail
    elif count_first:
        count = data[0]
        # Канонический вид: нечётная длина, коды помещаются, хвост нулевой.
        canon = (
            len(data) % 2 == 1
            and count * 2 <= len(data) - 1
            and all(byte == 0 for byte in data[1 + count * 2 :])
        )
        if not canon and len(data) % 2 == 0:
            # Чётная длина в счётчиковом формате невозможна — это блок
            # без счётчика.
            count_first = False
    if count_first:
        chunk = data[1 : 1 + data[0] * 2]
    else:
        chunk = data
    return [code for code in decode_dtc_list(chunk) if code != "P0000"]


def decode_vin_block(data: bytes) -> str:
    """Данные после 49 02 -> VIN (SAE J1979).

    Стандартный формат: <число записей> (<номер строки> <до 20 байт>)*.
    Часть ЭБУ (и эмуляторов) опускает номер строки — поддерживаем оба
    варианта: второй байт VIN всегда буква, а номера строк лежат в
    диапазоне 01–05, поэтому их легко отличить.
    """
    vin = b""
    try:
        count = data[0]
        if not 1 <= count <= 5:
            raise ValueError
        pos = 1
        for _ in range(count):
            if pos >= len(data):
                break
            if 1 <= data[pos] <= 5 and len(data) - pos - 1 >= 15:
                pos += 1  # порядковый номер строки присутствует
            vin += data[pos : pos + 20]
            pos += 20
    except (IndexError, ValueError):
        vin = data
    text = _ascii_only(vin)
    if not text:
        text = _ascii_only(data)
    return text[:17]


def _ascii_only(raw: bytes) -> str:
    return "".join(chr(byte) for byte in raw if 32 <= byte < 127)


def decode_mil_status(data: bytes) -> tuple[bool, int]:
    """Первый байт ответа 41 01 -> (горит ли чек, число кодов)."""
    if not data:
        raise ValueError("пустой ответ на 01 01")
    return bool(data[0] & 0x80), data[0] & 0x7F


@dataclass(frozen=True)
class PidValue:
    pid: int
    name: str
    value: float | str
    unit: str = ""

    def __str__(self) -> str:
        if isinstance(self.value, float):
            shown = f"{self.value:.2f}".rstrip("0").rstrip(".")
        else:
            shown = str(self.value)
        return f"{self.name}: {shown} {self.unit}".strip()


def decode_pid(pid: int, data: bytes) -> PidValue:
    """Разобрать ответ ЭБУ на запрос 01 <pid> (data — уже без 41 <pid>)."""
    name, unit = PID_NAMES.get(pid, (f"PID {pid:02X}", ""))

    if pid == 0x01:
        mil_on = bool(data[0] & 0x80)
        count = data[0] & 0x7F
        text = f"чек {'ГОРИТ' if mil_on else 'погашен'}, кодов: {count}"
        return PidValue(pid, "Статус мониторов", text)

    if pid in (0x04, 0x11):
        return PidValue(pid, name, data[0] * 100 / 255, unit)
    if pid in (0x05, 0x0F, 0x21, 0x46):
        # 0x21 — пробег, не температура; обрабатываем отдельно ниже
        if pid == 0x21:
            return PidValue(pid, name, (data[0] << 8) | data[1], unit)
        return PidValue(pid, name, data[0] - 40, unit)
    if pid in (0x06, 0x07, 0x08, 0x09):
        return PidValue(pid, name, (data[0] - 128) * 100 / 128, unit)
    if pid == 0x0C:
        return PidValue(pid, name, ((data[0] << 8) | data[1]) / 4, unit)
    if pid == 0x0D:
        return PidValue(pid, name, float(data[0]), unit)
    if pid == 0x10:
        return PidValue(pid, name, ((data[0] << 8) | data[1]) / 100, unit)
    if pid == 0x1F:
        return PidValue(pid, name, float((data[0] << 8) | data[1]), unit)
    if pid == 0x2F:
        return PidValue(pid, name, data[0] * 100 / 255, unit)
    if pid == 0x33:
        return PidValue(pid, name, float(data[0]), unit)
    if pid == 0x42:
        return PidValue(pid, name, ((data[0] << 8) | data[1]) / 1000, unit)

    return PidValue(pid, name, data.hex(" ").upper(), unit)
