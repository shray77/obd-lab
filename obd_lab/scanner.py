"""Сканер: то, что раньше было железной коробкой.

Работает поверх любого канала (Channel): виртуального, ELM327, python-can.
Сканер не знает, где он подключён, и не должен знать.
"""

from __future__ import annotations

from dataclasses import dataclass

from .channel import Channel
from .obd import (
    PidValue,
    decode_dtc_block,
    decode_mil_status,
    decode_pid,
    decode_vin_block,
    encode_dtc,
)
from .uds import BIT, Dtc, parse_read_dtc_response, parse_snapshot_response


class NegativeResponse(RuntimeError):
    def __init__(self, service: int, nrc: int) -> None:
        self.service = service
        self.nrc = nrc
        super().__init__(f"ЭБУ отказал: сервис {service:#04x}, NRC {nrc:#04x}")


@dataclass
class Snapshot:
    vin: str
    dtcs: list[Dtc]
    permanent: set[str]
    mil_on: bool
    dtc_count: int


class ScanTool:
    def __init__(self, channel: Channel) -> None:
        self.channel = channel

    def request(self, payload: bytes) -> bytes:
        response = self.channel.request(payload)
        if not response:
            raise TimeoutError(f"пустой ответ на {payload.hex(' ')}")
        if response[0] == 0x7F:
            nrc = response[2] if len(response) > 2 else 0
            raise NegativeResponse(response[1], nrc)
        return response

    # --- OBD-II -------------------------------------------------------
    def read_pid(self, pid: int) -> PidValue:
        return decode_pid(pid, self.read_pid_raw(pid))

    def read_pid_raw(self, pid: int) -> bytes:
        """Ответ 01 <pid> без заголовка: 41 <pid> -> данные."""
        response = self.request(bytes([0x01, pid]))
        if len(response) < 2 or response[0] != 0x41:
            raise ValueError(f"не ответ на PID {pid:02X}: {response.hex(' ')}")
        return response[2:]

    def _dtc_block(self, mode: int) -> list[str]:
        """Ответ 03/07/0A -> список кодов с учётом формата транспорта.

        На CAN после SID идёт байт-счётчик, на K-line его нет. Адаптер
        знает свой протокол после ATDPN и сообщает это атрибутом
        count_byte; виртуальный канал оставляет None — работает
        эвристика.
        """
        response = self.request(bytes([mode]))
        count_first = getattr(self.channel, "count_byte", None)
        return decode_dtc_block(response[1:], count_first=count_first)

    def read_confirmed_obd(self) -> list[str]:
        return self._dtc_block(0x03)

    def read_pending_obd(self) -> list[str]:
        return self._dtc_block(0x07)

    def read_permanent(self) -> set[str]:
        """Режим 0A. Поддерживают не все машины и не все адаптеры.

        Здесь это вспомогательный признак, поэтому отказ ЭБУ не должен
        обрушивать чтение кодов целиком: возвращаем пустое множество.
        """
        try:
            return set(self._dtc_block(0x0A))
        except (NegativeResponse, TimeoutError, ValueError):
            return set()

    # --- UDS ----------------------------------------------------------
    def read_dtcs(self) -> list[Dtc]:
        """Сначала пробуем UDS (статус-байт), иначе падаем на OBD.

        Дешёвый ELM327 и старые машины часто не умеют 0x19. Тогда
        статус восстанавливается приблизительно из режимов 03/07/0A.
        """
        try:
            return self._read_uds_dtcs()
        except (NegativeResponse, TimeoutError, ValueError):
            return self._read_obd_dtcs()

    def _read_uds_dtcs(self) -> list[Dtc]:
        permanent = self.read_permanent()
        response = self.request(b"\x19\x02\xFF")
        return [
            Dtc(dtc.code, dtc.status, permanent=dtc.code in permanent)
            for dtc in parse_read_dtc_response(response)
        ]

    def _read_obd_dtcs(self) -> list[Dtc]:
        confirmed = set(self.read_confirmed_obd())
        pending = set(self.read_pending_obd())
        permanent = self.read_permanent()

        result: list[Dtc] = []
        for code in sorted(confirmed | pending | permanent):
            if code == "P0000":
                continue
            status = BIT["testFailedSinceLastClear"]
            if code in confirmed:
                status |= BIT["confirmedDTC"] | BIT["warningIndicatorRequested"]
            if code in pending:
                status |= BIT["pendingDTC"]
            result.append(Dtc(code, status, permanent=code in permanent))
        return result

    def read_freeze(self, code: str) -> dict[str, float]:
        response = self.request(b"\x19\x04" + encode_dtc(code) + b"\x01")
        return parse_snapshot_response(response)

    def clear(self) -> None:
        self.request(b"\x14\xFF\xFF\xFF")

    def read_vin(self) -> str:
        try:
            response = self.request(b"\x22\xF1\x90")
            if response[0] == 0x62:
                return response[3:].decode("ascii", errors="replace").strip("\x00")
        except (NegativeResponse, TimeoutError):
            pass

        # Формат J1979: 49 02 <записей> (<номер строки> <до 20 байт>)*.
        response = self.request(b"\x09\x02")
        if response[0] != 0x49:
            raise ValueError(f"не ответ на 09 02: {response.hex(' ')}")
        return decode_vin_block(response[2:])

    def snapshot(self) -> Snapshot:
        mil_on, obd_count = decode_mil_status(self.read_pid_raw(0x01)[:1])
        dtcs = self.read_dtcs()
        count = sum(1 for dtc in dtcs if dtc.flags["confirmedDTC"]) or obd_count
        return Snapshot(
            vin=self.read_vin(),
            dtcs=dtcs,
            permanent={dtc.code for dtc in dtcs if dtc.permanent},
            mil_on=mil_on,
            dtc_count=count,
        )
