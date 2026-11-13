"""Виртуальная CAN-шина.

Заменяет железо: сканер и ЭБУ общаются кадрами ровно так же, как на
реальной машине, только кадры не идут по проводам, а передаются
внутри процесса. Позже на это же место встаёт python-can и адаптер.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

Handler = Callable[["Frame"], None]

MAX_DATA = 8  # классический CAN: 8 байт в кадре


@dataclass(frozen=True)
class Frame:
    arbitration_id: int
    data: bytes
    is_extended_id: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.arbitration_id <= 0x1FFFFFFF:
            raise ValueError(f"недопустимый CAN ID: {self.arbitration_id:#x}")
        if len(self.data) > MAX_DATA:
            raise ValueError(
                f"классический CAN вмещает {MAX_DATA} байт, "
                f"а пришло {len(self.data)}"
            )
        object.__setattr__(self, "data", bytes(self.data))


class VirtualBus:
    """Шина: каждый CAN ID обслуживается одним обработчиком."""

    def __init__(self) -> None:
        self._handlers: dict[int, Handler] = {}
        self.trace: list[Frame] = []

    def listen(self, can_id: int, handler: Handler) -> None:
        if can_id in self._handlers:
            raise ValueError(f"на CAN ID {can_id:#x} уже кто-то слушает")
        self._handlers[can_id] = handler

    def send(self, frame: Frame) -> None:
        self.trace.append(frame)
        handler = self._handlers.get(frame.arbitration_id)
        if handler is None:
            # Никто не слушает — это нормально, как и на живой шине.
            return
        handler(frame)

    def clear_trace(self) -> None:
        self.trace.clear()
