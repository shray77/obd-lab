"""Канал диагностики: абстракция над железом.

Сканеру всё равно, откуда пришёл ответ: из виртуальной шины, из ELM327
по COM-порту или из CANable через python-can. Ему нужен один метод:
отправить диагностическое сообщение и получить ответ.

Именно поэтому решение «свой сканер или готовый адаптер» перестаёт быть
архитектурным: это выбор одной реализации Channel.
"""

from __future__ import annotations

from typing import Protocol

from .canbus import VirtualBus
from .isotp import IsotpEndpoint


class Channel(Protocol):
    name: str

    def request(self, payload: bytes) -> bytes:
        """Отправить сообщение (OBD/UDS) и вернуть полный ответ ЭБУ."""
        ...

    def close(self) -> None:
        ...


class VirtualChannel:
    """Канал поверх виртуальной шины — для тестов и демо."""

    name = "virtual"

    def __init__(
        self,
        bus: VirtualBus,
        request_id: int = 0x7E0,
        response_id: int = 0x7E8,
    ) -> None:
        self._response: bytes | None = None
        self._isotp = IsotpEndpoint(
            bus,
            rx_id=response_id,
            tx_id=request_id,
            on_message=self._on_message,
        )

    def _on_message(self, payload: bytes) -> None:
        self._response = payload

    def request(self, payload: bytes) -> bytes:
        self._response = None
        self._isotp.send(payload)
        if self._response is None:
            raise TimeoutError(f"нет ответа на {payload.hex(' ')}")
        return self._response

    def close(self) -> None:
        return
