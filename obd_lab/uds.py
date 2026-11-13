"""UDS (ISO 14229): диагностика сверх экологии.

Нас интересуют три вещи:
  * 0x19 — чтение DTC вместе со статус-байтом (главный источник смысла);
  * 0x14 — стирание DTC;
  * 0x22 — чтение идентификатора (например, VIN).
"""

from __future__ import annotations

from dataclasses import dataclass

from .obd import decode_dtc, encode_dtc

# Биты статуса DTC (ISO 14229-1). Именно они отвечают на вопрос
# "это ложная ошибка или настоящая", а не сам код.
STATUS_BITS: dict[int, str] = {
    0: "testFailed",
    1: "testFailedThisOperationCycle",
    2: "pendingDTC",
    3: "confirmedDTC",
    4: "testNotCompletedSinceLastClear",
    5: "testFailedSinceLastClear",
    6: "testNotCompletedThisOperationCycle",
    7: "warningIndicatorRequested",
}

STATUS_NAMES_RU: dict[str, str] = {
    "testFailed": "не проходит прямо сейчас",
    "testFailedThisOperationCycle": "сбой в этом цикле зажигания",
    "pendingDTC": "ждёт подтверждения",
    "confirmedDTC": "подтверждена",
    "testNotCompletedSinceLastClear": "монитор ещё не отработал",
    "testFailedSinceLastClear": "была после последнего сброса",
    "testNotCompletedThisOperationCycle": "монитор не отработал в этом цикле",
    "warningIndicatorRequested": "просит зажечь чек",
}

BIT = {name: 1 << bit for bit, name in STATUS_BITS.items()}


def status_flags(raw: int) -> dict[str, bool]:
    return {name: bool(raw & mask) for name, mask in BIT.items()}


def status_text(raw: int) -> str:
    active = [STATUS_NAMES_RU[name] for name, on in status_flags(raw).items() if on]
    return ", ".join(active) if active else "нет активных признаков"


@dataclass(frozen=True)
class Dtc:
    code: str
    status: int
    permanent: bool = False

    @property
    def flags(self) -> dict[str, bool]:
        return status_flags(self.status)

    def __str__(self) -> str:
        return f"{self.code} [{self.status:02X}]"


def parse_read_dtc_response(payload: bytes) -> list[Dtc]:
    """59 02 <mask> <code...> -> список DTC."""
    if len(payload) < 3 or payload[0] != 0x59 or payload[1] != 0x02:
        raise ValueError(f"не ответ на 19 02: {payload.hex(' ')}")
    body = payload[3:]
    out: list[Dtc] = []
    for i in range(0, len(body) - 2, 3):
        out.append(Dtc(decode_dtc(body[i], body[i + 1]), body[i + 2]))
    return out


def parse_read_dtc_request(payload: bytes) -> tuple[int, int]:
    """Вернуть (подфункция, маска статуса)."""
    if len(payload) < 3 or payload[0] != 0x19:
        raise ValueError("ожидался запрос 19 <sub> <mask>")
    return payload[1], payload[2]


def build_read_dtc_response(records: list[tuple[str, int]], mask: int = 0xFF) -> bytes:
    """Собрать ответ 59 02 <availability> <code,status>..."""
    out = bytearray([0x59, 0x02, mask])
    for code, status in records:
        if status & mask:
            out += encode_dtc(code) + bytes([status])
    return bytes(out)


def build_snapshot_response(code: str, status: int, frame: dict[str, float]) -> bytes:
    """Упрощённый freeze frame: 59 04 <code> <status> <seq> <данные>."""
    rpm = int(frame.get("rpm", 0))
    out = bytearray([0x59, 0x04])
    out += encode_dtc(code)
    out += bytes([status, 0x01])
    out += bytes([(rpm >> 8) & 0xFF, rpm & 0xFF])
    out += bytes([int(frame.get("speed", 0)) & 0xFF])
    out += bytes([(int(frame.get("coolant_c", 0)) + 40) & 0xFF])
    out += bytes([(int(frame.get("ambient_c", 0)) + 40) & 0xFF])
    out += bytes([int(round(frame.get("battery_v", 0) * 10)) & 0xFF])
    return bytes(out)


def parse_snapshot_response(payload: bytes) -> dict[str, float]:
    if len(payload) < 12 or payload[0] != 0x59 or payload[1] != 0x04:
        raise ValueError("не ответ на 19 04")
    # 59 04 <code:2> <status> <seq> <rpm:2> <speed> <coolant> <ambient> <volt>
    rpm = (payload[6] << 8) | payload[7]
    return {
        "rpm": float(rpm),
        "speed": float(payload[8]),
        "coolant_c": float(payload[9] - 40),
        "ambient_c": float(payload[10] - 40),
        "battery_v": payload[11] / 10,
    }


def build_negative_response(service: int, nrc: int = 0x11) -> bytes:
    return bytes([0x7F, service, nrc])


NRC_TEXT = {
    0x11: "сервис не поддерживается",
    0x12: "подфункция не поддерживается",
    0x22: "условия не выполнены",
    0x31: "запрос вне диапазона",
    0x33: "требуется разблокировка",
}
