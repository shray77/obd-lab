"""Канал через адаптер ELM327 (и совместимые: vLinker, OBDLink, клоны).

Адаптер сам делает ISO-TP, поэтому здесь мы работаем на уровне готовых
диагностических сообщений: отправили байты — получили байты.

Нужен pyserial:

    python -m pip install pyserial

Порт может быть обычным COM-портом или URL:
    COM5
    socket://127.0.0.1:35000   (для тестов с эмулятором адаптера)
"""

from __future__ import annotations

import time

from .isotp import decode as isotp_decode

INIT_COMMANDS = (
    "ATZ",     # сброс адаптера
    "ATE0",    # эхо выключено
    "ATL0",    # без переводов строк
    "ATS0",    # без пробелов
    "ATH1",    # заголовки CAN включены
    "ATSP0",   # автоопределение протокола
)

# Номера протоколов ELM327 (ATDPN): 0 — авто, 1–5 — не CAN,
# 6–C — CAN 11/29 бит. От этого зависит формат ответа 43/47/4A:
# на CAN после SID идёт байт-счётчик DTC, на K-line — нет.
PROTOCOL_NAMES = {
    0x0: "авто",
    0x1: "J1850 PWM",
    0x2: "J1850 VPW",
    0x3: "ISO 9141-2 (K-line)",
    0x4: "ISO 14230-4 KWP 5 бод",
    0x5: "ISO 14230-4 KWP быстрый",
    0x6: "CAN 11 бит, 500 кбит/с",
    0x7: "CAN 29 бит, 500 кбит/с",
    0x8: "CAN 11 бит, 250 кбит/с",
    0x9: "CAN 29 бит, 250 кбит/с",
    0xA: "CAN 11 бит, 500 кбит/с (пользовательская)",
    0xB: "CAN 29 бит, 250 кбит/с (пользовательская)",
    0xC: "CAN 11 бит, 250 кбит/с (пользовательская)",
}

NOISE = ("SEARCHING", "NODATA", "STOPPED", "BUSINIT", "ERROR", "UNABLE")


class Elm327Channel:
    name = "elm327"

    def __init__(
        self,
        port: str,
        baudrate: int = 38400,
        timeout: float = 2.0,
        response_id: int | None = 0x7E8,
    ) -> None:
        """response_id — какой ЭБУ слушать.

        По умолчанию 0x7E8 (моторный): на широковещательный запрос
        09 02 отвечают несколько блоков с разными VIN, и без фильтра
        винт каждый раз берётся от первого попавшегося.
        """
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - зависит от окружения
            raise RuntimeError(
                "нужен pyserial: python -m pip install pyserial"
            ) from exc

        self.ser = serial.serial_for_url(port, baudrate=baudrate, timeout=timeout)
        self.response_id = response_id
        self.count_byte: bool | None = None  # определится по ATDPN
        for command in INIT_COMMANDS:
            self._command(command)
            if command == "ATZ":
                time.sleep(1.0)  # адаптеру нужно время на самотест
        try:
            self.protocol()
        except Exception:  # noqa: BLE001 — протокол узнаем и позже
            pass

    # ------------------------------------------------------------------
    def _command(self, text: str) -> str:
        self.ser.reset_input_buffer()
        self.ser.write((text + "\r").encode("ascii"))
        chunks: list[bytes] = []
        while True:
            char = self.ser.read(1)
            if not char:
                break
            chunks.append(char)
            if char == b">":
                break
        return b"".join(chunks).decode("ascii", errors="replace")

    def voltage(self) -> float | None:
        text = self._command("ATRV")
        digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
        try:
            return float(digits) if digits else None
        except ValueError:
            return None

    def protocol(self) -> str:
        """ATDPN: номер протокола. Заодно определяет формат ответов 43/47/4A."""
        text = self._command("ATDPN").strip().strip(">").strip()
        hex_chars = [ch for ch in text if ch in "0123456789ABCDEF"]
        if not hex_chars:
            self.count_byte = None
            return text
        number = int(hex_chars[-1], 16)
        if number == 0:
            self.count_byte = None  # авто: ещё не определился
        else:
            self.count_byte = 0x6 <= number <= 0xC  # только CAN несёт счётчик
        return PROTOCOL_NAMES.get(number, f"протокол {number:#x}")

    # ------------------------------------------------------------------
    def request(self, payload: bytes) -> bytes:
        command = payload.hex().upper()
        text = self._command(command)
        frames = self._parse(text, echo=command)
        if not frames:
            raise TimeoutError(
                f"ELM327 не ответил на {payload.hex(' ')}: {text.strip()!r}"
            )
        return isotp_decode(frames)

    def _parse(self, text: str, echo: str = "") -> list[bytes]:
        groups: dict[str, list[bytes]] = {}
        order: list[str] = []

        for raw_line in text.replace("\r", "\n").split("\n"):
            line = raw_line.strip().lstrip(">").strip()
            if not line:
                continue
            compact = line.replace(" ", "").upper()
            if compact == echo:
                continue
            if any(word in compact for word in NOISE):
                continue
            if not all(char in "0123456789ABCDEF" for char in compact):
                continue

            # ATH1: 3 hex-символа заголовка для 11-бит CAN.
            if len(compact) % 2 == 1:
                header, body = compact[:3], compact[3:]
            else:
                header, body = "", compact

            if len(body) < 2 or len(body) % 2:
                continue
            if header not in groups:
                groups[header] = []
                order.append(header)
            groups[header].append(bytes.fromhex(body))

        if not order:
            return []
        header = order[0]
        if self.response_id is not None:
            wanted = f"{self.response_id:03X}"
            if wanted in groups:
                header = wanted
        return groups[header]

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:  # noqa: BLE001
            pass
