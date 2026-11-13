"""ISO-TP (ISO 15765-2): транспорт для диагностических сообщений.

CAN-кадр вмещает 8 байт. Диагностическое сообщение бывает длиннее
(например, VIN или список ошибок), поэтому оно режется на кадры:

  Single Frame (SF)      0x0L + данные, до 7 байт
  First Frame (FF)       0x1H 0xLL + первые 6 байт
  Consecutive Frame (CF) 0x2N + следующие 7 байт
  Flow Control (FC)      0x30 <BS> <STmin> ("шлю дальше")

Получатель обязан ответить Flow Control на First Frame и после каждого
блока из BS кадров, если BS != 0. Отправитель не шлёт ни один CF,
пока не получит FC. STmin — минимальная пауза между CF в миллисекундах.

Здесь нет ожидания на уровне шины: виртуальная шина синхронная, FC
прилетает прямо во время отправки FF. Счётчик FC вместо события —
чтобы один лишний FC не потерялся и код работал и на потоках позже
(CANable/python-can).
"""

from __future__ import annotations

import time
from typing import Callable

from .canbus import Frame, VirtualBus

MAX_SF_PAYLOAD = 7
MAX_MESSAGE = 0xFFF  # 12 бит длины в First Frame
FC_TIMEOUT = 0.5  # секунды

MessageHandler = Callable[[bytes], None]


def encode(payload: bytes) -> list[bytes]:
    """Разложить сообщение на CAN-кадры (без Flow Control)."""
    n = len(payload)
    if n == 0:
        raise ValueError("пустое диагностическое сообщение")
    if n > MAX_MESSAGE:
        raise ValueError(f"сообщение длиннее {MAX_MESSAGE} байт: {n}")

    if n <= MAX_SF_PAYLOAD:
        return [bytes([n]) + payload]

    frames = [bytes([0x10 | (n >> 8), n & 0xFF]) + payload[:6]]
    rest = payload[6:]
    seq = 1
    for offset in range(0, len(rest), 7):
        frames.append(bytes([0x20 | (seq & 0x0F)]) + rest[offset : offset + 7])
        seq += 1
    return frames


def decode(frames: list[bytes]) -> bytes:
    """Собрать сообщение из готового списка кадров (для тестов)."""
    if not frames:
        raise ValueError("нет кадров")
    first = frames[0]
    kind = first[0] >> 4
    if kind == 0x0:
        return bytes(first[1 : 1 + (first[0] & 0x0F)])
    if kind != 0x1:
        raise ValueError("последовательность должна начинаться с SF или FF")
    total = ((first[0] & 0x0F) << 8) | first[1]
    out = bytearray(first[2:])
    for frame in frames[1:]:
        if frame[0] >> 4 != 0x2:
            raise ValueError("ожидался Consecutive Frame")
        out += frame[1:]
    return bytes(out[:total])


class IsotpEndpoint:
    """Один участник шины. Принимает сообщения на rx_id, шлёт на tx_id."""

    def __init__(
        self,
        bus: VirtualBus,
        rx_id: int,
        tx_id: int,
        on_message: MessageHandler | None = None,
        fc_timeout: float = FC_TIMEOUT,
    ) -> None:
        self.bus = bus
        self.rx_id = rx_id
        self.tx_id = tx_id
        self.on_message = on_message
        self.fc_timeout = fc_timeout
        self.last_message: bytes | None = None
        self._pending: bytearray | None = None
        self._expected = 0
        self._seq = 1
        # Flow Control от собеседника
        self._fc_count = 0
        self._fc_bs = 0  # block size: 0 = слать всё до конца
        self._fc_stmin = 0  # пауза между CF, мс
        self.bus.listen(rx_id, self._receive)

    def send(self, payload: bytes) -> None:
        frames = encode(payload)
        if len(frames) == 1:
            self.bus.send(Frame(self.tx_id, frames[0]))
            return

        # First Frame, затем обязательное ожидание Flow Control.
        seen = self._fc_count
        self.bus.send(Frame(self.tx_id, frames[0]))
        block_size, stmin = self._wait_fc(seen)

        sent = 0
        index = 1
        while index < len(frames):
            self.bus.send(Frame(self.tx_id, frames[index]))
            index += 1
            sent += 1
            if stmin:
                time.sleep(stmin * 0.001)
            if block_size and sent >= block_size and index < len(frames):
                block_size, stmin = self._wait_fc(self._fc_count)
                sent = 0

    def _wait_fc(self, seen: int) -> tuple[int, int]:
        """Дождаться очередного FC и вернуть (block size, stmin)."""
        start = time.monotonic()
        while self._fc_count <= seen:
            if time.monotonic() - start > self.fc_timeout:
                raise TimeoutError("нет Flow Control после First Frame")
            time.sleep(0.0005)
        return self._fc_bs, self._fc_stmin

    def _receive(self, frame: Frame) -> None:
        data = frame.data
        if not data:
            return
        kind = data[0] >> 4

        if kind == 0x0:  # Single Frame
            self._deliver(bytes(data[1 : 1 + (data[0] & 0x0F)]))
        elif kind == 0x1:  # First Frame
            self._expected = ((data[0] & 0x0F) << 8) | data[1]
            self._pending = bytearray(data[2:])
            self._seq = 1
            # Flow Control: слать дальше без ограничения блоков и пауз.
            self.bus.send(Frame(self.tx_id, bytes([0x30, 0x00, 0x00, 0x00, 0, 0, 0, 0])))
        elif kind == 0x2:  # Consecutive Frame
            if self._pending is None:
                return
            if (data[0] & 0x0F) != (self._seq & 0x0F):
                self._pending = None
                return
            self._seq += 1
            self._pending += data[1:]
            if len(self._pending) >= self._expected:
                payload = bytes(self._pending[: self._expected])
                self._pending = None
                self._deliver(payload)
        elif kind == 0x3:  # Flow Control
            self._fc_bs = data[1] if len(data) > 1 else 0
            self._fc_stmin = data[2] if len(data) > 2 else 0
            self._fc_count += 1

    def _deliver(self, payload: bytes) -> None:
        self.last_message = payload
        if self.on_message is not None:
            self.on_message(payload)
