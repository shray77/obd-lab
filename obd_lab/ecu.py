"""Виртуальный ЭБУ.

Отвечает на OBD-II и UDS как настоящий блок управления, только вместо
железа — таймер циклов зажигания. Это позволяет воспроизводить
сценарии: холодный пуск, слабый АКБ, разовая помеха, заменённая деталь.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .canbus import VirtualBus
from .isotp import IsotpEndpoint
from .obd import SUPPORTED_01_20, encode_dtc, supported_pids_mask
from .uds import (
    BIT,
    build_negative_response,
    build_read_dtc_response,
    build_snapshot_response,
)

Conditions = dict[str, float]
Trigger = Callable[[Conditions], bool]


@dataclass
class DtcRecord:
    code: str
    permanent: bool = False
    status: int = 0
    occurrences: int = 0
    first_cycle: int | None = None
    last_cycle: int | None = None
    pass_streak: int = 0
    freeze: dict[str, float] | None = None


@dataclass
class Monitor:
    """Правило, по которому неисправность возникает в симуляции."""

    code: str
    title: str
    trigger: Trigger
    permanent: bool = False


@dataclass
class VirtualEcu:
    bus: VirtualBus
    request_id: int = 0x7E0
    response_id: int = 0x7E8
    vin: str = "XTA211500M0000001"
    monitors: list[Monitor] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.isotp = IsotpEndpoint(
            self.bus,
            rx_id=self.request_id,
            tx_id=self.response_id,
            on_message=self.handle,
        )
        self.records: dict[str, DtcRecord] = {}
        self.cycle = 0
        self.conditions: Conditions = {}
        self.live: dict[str, float] = {
            "rpm": 0.0,
            "speed": 0.0,
            "coolant_c": 20.0,
            "ambient_c": 20.0,
            "battery_v": 12.4,
            "load": 20.0,
            "throttle": 12.0,
            "maf": 3.2,
            "ltft": 2.0,
            "stft": 0.0,
        }

    # ------------------------------------------------------------------
    # Симуляция
    # ------------------------------------------------------------------
    def advance_cycle(
        self,
        *,
        ambient_c: float = 20.0,
        battery_v: float = 12.4,
        coolant_c: float | None = None,
        rpm: float = 820.0,
        speed: float = 0.0,
        cold_start: bool = False,
    ) -> dict[str, DtcRecord]:
        """Один цикл зажигания: мониторы отработали, статусы обновились."""
        self.cycle += 1
        coolant = (ambient_c + 2) if cold_start else (coolant_c if coolant_c is not None else 88.0)
        self.conditions = {
            "cycle": float(self.cycle),
            "ambient_c": ambient_c,
            "battery_v": battery_v,
            "coolant_c": coolant,
            "rpm": rpm,
            "speed": speed,
            "cold_start": 1.0 if cold_start else 0.0,
        }
        self.live.update(
            rpm=rpm,
            speed=speed,
            coolant_c=coolant,
            ambient_c=ambient_c,
            battery_v=battery_v,
            load=18.0 if speed == 0 else 34.0,
            maf=3.1 if speed == 0 else 12.0,
        )

        for monitor in self.monitors:
            record = self.records.get(monitor.code)
            if record is None:
                record = DtcRecord(
                    code=monitor.code,
                    permanent=monitor.permanent,
                    status=(
                        BIT["testNotCompletedSinceLastClear"]
                        | BIT["testNotCompletedThisOperationCycle"]
                    ),
                )
                self.records[monitor.code] = record

            failed = bool(monitor.trigger(self.conditions))
            record.status &= ~(
                BIT["testNotCompletedSinceLastClear"]
                | BIT["testNotCompletedThisOperationCycle"]
            )

            if failed:
                record.occurrences += 1
                if record.first_cycle is None:
                    record.first_cycle = self.cycle
                record.last_cycle = self.cycle
                record.pass_streak = 0
                record.status |= (
                    BIT["testFailed"]
                    | BIT["testFailedThisOperationCycle"]
                    | BIT["testFailedSinceLastClear"]
                    | BIT["pendingDTC"]
                )
                if record.occurrences >= 2:
                    record.status |= BIT["confirmedDTC"] | BIT["warningIndicatorRequested"]
                if record.freeze is None:
                    record.freeze = {
                        "rpm": rpm,
                        "speed": speed,
                        "coolant_c": coolant,
                        "ambient_c": ambient_c,
                        "battery_v": battery_v,
                    }
            else:
                record.status &= ~(
                    BIT["testFailed"] | BIT["testFailedThisOperationCycle"] | BIT["pendingDTC"]
                )
                record.pass_streak += 1
                if record.pass_streak >= 3:
                    record.status &= ~(
                        BIT["confirmedDTC"] | BIT["warningIndicatorRequested"]
                    )
                # Permanent DTC уходит сам, но очень небыстро: десятки
                # успешных циклов, а не пара поездок.
                if record.permanent and record.pass_streak >= 40:
                    del self.records[monitor.code]

        # Коррекции топлива следуют за реальной неисправностью.
        lean = self.records.get("P0171")
        if lean is not None and lean.status & BIT["testFailed"]:
            self.live["ltft"] = 24.0
            self.live["stft"] = 7.0
        else:
            self.live["ltft"] = 2.5
            self.live["stft"] = 0.0

        return dict(self.records)

    def clear(self) -> tuple[list[str], list[str]]:
        """Стереть всё, что разрешено. Permanent DTC остаются."""
        removed, kept = [], []
        for code in list(self.records):
            if self.records[code].permanent:
                kept.append(code)
            else:
                removed.append(code)
                del self.records[code]
        return removed, kept

    def dtc_list(self, *, only_confirmed: bool = False, only_permanent: bool = False) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for code, record in self.records.items():
            if record.status == 0:
                continue
            if only_confirmed and not record.status & BIT["confirmedDTC"]:
                continue
            if only_permanent and not record.permanent:
                continue
            out.append((code, record.status))
        return out

    # ------------------------------------------------------------------
    # Обработка запросов
    # ------------------------------------------------------------------
    def handle(self, payload: bytes) -> None:
        if not payload:
            return
        try:
            response = self._dispatch(payload)
        except Exception:  # noqa: BLE001 — виртуальный ЭБУ не должен падать от кривого запроса
            response = build_negative_response(payload[0], 0x22)
        if response is not None:
            self.isotp.send(response)

    def _dispatch(self, payload: bytes) -> bytes | None:
        service = payload[0]

        if service == 0x01:
            return self._obd_live(payload)
        if service == 0x03:
            return b"\x43" + self._obd_dtc_body(only_confirmed=True)
        if service == 0x07:
            return b"\x47" + self._obd_dtc_body(pending=True)
        if service == 0x0A:
            return b"\x4A" + self._obd_dtc_body(only_permanent=True)
        if service == 0x04:
            self.clear()
            return b"\x44"
        if service == 0x09:
            return self._obd_info(payload)

        if service == 0x10:
            session = payload[1] if len(payload) > 1 else 0x01
            return bytes([0x50, session, 0x00, 0x32, 0x01, 0xF4])
        if service == 0x14:
            self.clear()
            return b"\x54"
        if service == 0x19:
            return self._uds_dtc(payload)
        if service == 0x22:
            return self._uds_read_did(payload)

        return build_negative_response(service, 0x11)

    # --- OBD-II -------------------------------------------------------
    def _obd_live(self, payload: bytes) -> bytes:
        if len(payload) < 2:
            return build_negative_response(0x01, 0x13)
        pid = payload[1]

        if pid == 0x00:
            return bytes([0x41, 0x00]) + supported_pids_mask(list(SUPPORTED_01_20))

        data = self._pid_data(pid)
        if data is None:
            return build_negative_response(0x01, 0x12)
        return bytes([0x41, pid]) + data

    def _pid_data(self, pid: int) -> bytes | None:
        live = self.live
        if pid == 0x01:
            mil = any(r.status & BIT["warningIndicatorRequested"] for r in self.records.values())
            count = sum(1 for r in self.records.values() if r.status & BIT["confirmedDTC"])
            return bytes([(0x80 if mil else 0) | min(count, 0x7F), 0x07, 0x65, 0x00])
        if pid == 0x04:
            return bytes([_clamp(live["load"] * 255 / 100)])
        if pid == 0x05:
            return bytes([_clamp(live["coolant_c"] + 40)])
        if pid in (0x06, 0x08):
            return bytes([_trim(live["stft"])])
        if pid in (0x07, 0x09):
            return bytes([_trim(live["ltft"])])
        if pid == 0x0C:
            rpm = int(live["rpm"] * 4)
            return bytes([(rpm >> 8) & 0xFF, rpm & 0xFF])
        if pid == 0x0D:
            return bytes([_clamp(live["speed"])])
        if pid == 0x0F:
            return bytes([_clamp(live["ambient_c"] + 40)])
        if pid == 0x10:
            maf = int(live["maf"] * 100)
            return bytes([(maf >> 8) & 0xFF, maf & 0xFF])
        if pid == 0x11:
            return bytes([_clamp(live["throttle"] * 255 / 100)])
        if pid == 0x1F:
            return b"\x00\x64"
        if pid == 0x21:
            return b"\x00\x00"
        if pid == 0x2F:
            return bytes([_clamp(58)])
        if pid == 0x33:
            return bytes([101])
        if pid == 0x42:
            mv = int(live["battery_v"] * 1000)
            return bytes([(mv >> 8) & 0xFF, mv & 0xFF])
        if pid == 0x46:
            return bytes([_clamp(live["ambient_c"] + 40)])
        return None

    def _obd_dtc_body(
        self,
        *,
        only_confirmed: bool = False,
        pending: bool = False,
        only_permanent: bool = False,
    ) -> bytes:
        """Тело ответа 43/47/4A: <число DTC> <код> <код> ... (ISO 15765-4)."""
        codes = self.dtc_list(
            only_confirmed=only_confirmed, only_permanent=only_permanent
        )
        if pending:
            codes = [(c, s) for c, s in codes if s & BIT["pendingDTC"]]
        body = bytearray([len(codes)])
        for code, _status in codes:
            body += encode_dtc(code)
        return bytes(body)

    def _obd_info(self, payload: bytes) -> bytes:
        if len(payload) < 2:
            return build_negative_response(0x09, 0x13)
        if payload[1] == 0x00:
            return b"\x49\x00" + supported_pids_mask([0x02])
        if payload[1] == 0x02:
            # Формат J1979: <число записей> <номер строки> <до 20 байт VIN>.
            vin = self.vin.encode("ascii")
            return b"\x49\x02\x01\x01" + vin
        return build_negative_response(0x09, 0x12)

    # --- UDS ----------------------------------------------------------
    def _uds_dtc(self, payload: bytes) -> bytes:
        if len(payload) < 3:
            return build_negative_response(0x19, 0x13)
        sub = payload[1]

        if sub == 0x02:
            mask = payload[2]
            return build_read_dtc_response(self.dtc_list(), mask=mask)

        if sub == 0x04:
            from .obd import decode_dtc

            if len(payload) < 4:
                return build_negative_response(0x19, 0x13)
            code = decode_dtc(payload[2], payload[3])
            record = self.records.get(code)
            if record is None or record.freeze is None:
                return build_negative_response(0x19, 0x31)
            return build_snapshot_response(code, record.status, record.freeze)

        return build_negative_response(0x19, 0x12)

    def _uds_read_did(self, payload: bytes) -> bytes:
        if len(payload) < 3:
            return build_negative_response(0x22, 0x13)
        did = (payload[1] << 8) | payload[2]
        if did == 0xF190:
            return b"\x62\xF1\x90" + self.vin.encode("ascii")
        if did == 0xF187:
            return b"\x62\xF1\x87" + b"LAB-ECU-0001"
        return build_negative_response(0x22, 0x31)


def _clamp(value: float, low: int = 0, high: int = 255) -> int:
    return max(low, min(high, int(round(value))))


def _trim(percent: float) -> int:
    return _clamp(percent * 128 / 100 + 128)
