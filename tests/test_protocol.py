from __future__ import annotations

import unittest

from obd_lab.canbus import VirtualBus
from obd_lab.channel import VirtualChannel
from obd_lab.ecu import Monitor, VirtualEcu
from obd_lab.isotp import decode, encode
from obd_lab.knowledge import KnowledgeBase
from obd_lab.obd import decode_dtc, decode_pid, encode_dtc
from obd_lab.scanner import ScanTool
from obd_lab.uds import BIT, status_flags
from obd_lab.verdict import verdict


class DtcCodecTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        for code in ("P0171", "P0420", "P0562", "U0100", "C1234", "B0020"):
            raw = encode_dtc(code)
            self.assertEqual(decode_dtc(raw[0], raw[1]), code)

    def test_p0171_bytes(self) -> None:
        self.assertEqual(encode_dtc("P0171"), bytes([0x01, 0x71]))


class IsotpTest(unittest.TestCase):
    def test_single_frame(self) -> None:
        frames = encode(b"\x01\x0C")
        self.assertEqual(frames, [b"\x02\x01\x0C"])
        self.assertEqual(decode(frames), b"\x01\x0C")

    def test_multi_frame(self) -> None:
        payload = bytes(range(20))
        frames = encode(payload)
        self.assertGreater(len(frames), 1)
        self.assertEqual(decode(frames), payload)

    def test_sequence_numbers_wrap(self) -> None:
        payload = bytes(range(200))
        self.assertEqual(decode(encode(payload)), payload)


class IsotpFlowControlTest(unittest.TestCase):
    def test_receiver_sends_flow_control(self) -> None:
        bus = VirtualBus()
        from obd_lab.isotp import IsotpEndpoint

        received: list[bytes] = []
        IsotpEndpoint(bus, rx_id=0x7E0, tx_id=0x7E8, on_message=received.append)
        sender = IsotpEndpoint(bus, rx_id=0x7E8, tx_id=0x7E0)
        sender.send(bytes(range(20)))

        self.assertEqual(received, [bytes(range(20))])
        flow_controls = [f for f in bus.trace if f.data[:1] == b"\x30"]
        # FC шлёт получатель со своего адреса (у ЭБУ это 0x7E8).
        self.assertEqual(flow_controls[0].arbitration_id, 0x7E8)
        self.assertEqual(flow_controls[0].data[1], 0x00)  # block size: без блоков

    def test_missing_flow_control_times_out(self) -> None:
        from obd_lab.isotp import IsotpEndpoint

        bus = VirtualBus()
        sender = IsotpEndpoint(bus, rx_id=0x7E8, tx_id=0x7E0, fc_timeout=0.05)
        with self.assertRaises(TimeoutError):
            sender.send(bytes(range(20)))

    def test_single_frame_does_not_need_flow_control(self) -> None:
        from obd_lab.isotp import IsotpEndpoint

        bus = VirtualBus()
        received: list[bytes] = []
        IsotpEndpoint(bus, rx_id=0x7E0, tx_id=0x7E8, on_message=received.append)
        sender = IsotpEndpoint(bus, rx_id=0x7E8, tx_id=0x7E0)
        sender.send(b"\x01\x0C")
        self.assertEqual(received, [b"\x01\x0C"])


class PidTest(unittest.TestCase):
    def test_rpm(self) -> None:
        value = decode_pid(0x0C, b"\x1A\xF8")  # 6904 / 4 = 1726
        self.assertAlmostEqual(float(value.value), 1726.0)

    def test_coolant(self) -> None:
        value = decode_pid(0x05, b"\x7B")  # 123 - 40
        self.assertEqual(float(value.value), 83.0)

    def test_fuel_trim(self) -> None:
        value = decode_pid(0x07, b"\x9E")  # 158 -> +23.4%
        self.assertAlmostEqual(float(value.value), 23.4375, places=2)

    def test_module_voltage_is_two_byte(self) -> None:
        value = decode_pid(0x42, b"\x30\x78")  # 12408 / 1000
        self.assertAlmostEqual(float(value.value), 12.408)

    def test_fuel_level_is_scaled(self) -> None:
        value = decode_pid(0x2F, b"\x96")  # 150 * 100 / 255
        self.assertAlmostEqual(float(value.value), 58.82, places=2)

    def test_mil_status(self) -> None:
        from obd_lab.obd import decode_mil_status

        self.assertEqual(decode_mil_status(b"\x82"), (True, 2))
        self.assertEqual(decode_mil_status(b"\x02"), (False, 2))


class DtcBlockTest(unittest.TestCase):
    """Ответы 43/47/4A: CAN несёт счётчик, K-line — нет."""

    def test_can_with_count_and_padding(self) -> None:
        from obd_lab.obd import decode_dtc_block

        data = bytes.fromhex("02 0171 2187 0000".replace(" ", ""))
        self.assertEqual(decode_dtc_block(data), ["P0171", "P2187"])

    def test_can_single_code_padded(self) -> None:
        from obd_lab.obd import decode_dtc_block

        data = bytes.fromhex("0101 71 0000 0000".replace(" ", ""))
        self.assertEqual(decode_dtc_block(data), ["P0171"])

    def test_kline_without_count(self) -> None:
        from obd_lab.obd import decode_dtc_block

        self.assertEqual(decode_dtc_block(bytes.fromhex("0171")), ["P0171"])
        self.assertEqual(
            decode_dtc_block(bytes.fromhex("017121870247".replace(" ", ""))),
            ["P0171", "P2187", "P0247"],
        )

    def test_explicit_count_first(self) -> None:
        from obd_lab.obd import decode_dtc_block

        self.assertEqual(decode_dtc_block(bytes.fromhex("010171"), count_first=True), ["P0171"])

    def test_explicit_count_first_real_can_multiframe(self) -> None:
        from obd_lab.obd import decode_dtc_block

        # Реальный CAN: 43 03 01 71 21 87 04 20 — счётчик + 3 кода.
        data = bytes.fromhex("03017121870420".replace(" ", ""))
        self.assertEqual(
            decode_dtc_block(data, count_first=True),
            ["P0171", "P2187", "P0420"],
        )

    def test_explicit_count_first_but_block_without_count(self) -> None:
        """Часть ЭБУ (и наш тестовый эмулятор) шлёт CAN-блок без счётчика.

        Чётная длина блока в счётчиковом формате невозможна — значит,
        первый код перепутан со счётчиком, читаем без него.
        """
        from obd_lab.obd import decode_dtc_block

        data = bytes.fromhex("0171 2187 0420".replace(" ", ""))
        self.assertEqual(
            decode_dtc_block(data, count_first=True),
            ["P0171", "P2187", "P0420"],
        )
        # Один код без счётчика: 43 01 71 -> не один C-cод, а P0171.
        self.assertEqual(decode_dtc_block(bytes.fromhex("0171"), count_first=True), ["P0171"])

    def test_explicit_count_first_empty(self) -> None:
        from obd_lab.obd import decode_dtc_block

        # Эмулятор на пустой блок отвечает 43 00 (без счётчика "00").
        self.assertEqual(decode_dtc_block(bytes.fromhex("00"), count_first=True), [])

    def test_empty_and_zero_padding(self) -> None:
        from obd_lab.obd import decode_dtc_block

        self.assertEqual(decode_dtc_block(b""), [])
        self.assertEqual(decode_dtc_block(bytes.fromhex("000000")), [])

    def test_vin_block_real_format(self) -> None:
        from obd_lab.obd import decode_vin_block

        data = b"\x01\x01" + b"XTA211500M0000001"
        self.assertEqual(decode_vin_block(data), "XTA211500M0000001")

    def test_vin_block_fallback_on_garbage(self) -> None:
        from obd_lab.obd import decode_vin_block

        self.assertEqual(decode_vin_block(b"X" * 17), "X" * 17)


class VerdictTest(unittest.TestCase):
    def test_active_is_not_clearable(self) -> None:
        self.assertEqual(verdict(BIT["testFailed"]).level, "do_not_clear")

    def test_historical_is_clearable(self) -> None:
        self.assertEqual(verdict(BIT["testFailedSinceLastClear"]).level, "clear_safe")

    def test_permanent_is_blocked(self) -> None:
        self.assertEqual(verdict(BIT["confirmedDTC"], permanent=True).level, "blocked")

    def test_confirmed_not_active(self) -> None:
        self.assertEqual(verdict(BIT["confirmedDTC"]).level, "confirmed")

    def test_status_flags(self) -> None:
        flags = status_flags(0x09)
        self.assertTrue(flags["testFailed"])
        self.assertTrue(flags["confirmedDTC"])


class EndToEndTest(unittest.TestCase):
    def test_read_and_clear(self) -> None:
        bus = VirtualBus()
        ecu = VirtualEcu(
            bus,
            monitors=[Monitor("P0171", "lean", lambda conditions: True)],
        )
        tool = ScanTool(VirtualChannel(bus))
        ecu.advance_cycle()

        dtcs = tool.read_dtcs()
        self.assertEqual([dtc.code for dtc in dtcs], ["P0171"])
        self.assertTrue(dtcs[0].flags["testFailed"])

        tool.clear()
        self.assertEqual(tool.read_dtcs(), [])

    def test_permanent_survives_clear(self) -> None:
        bus = VirtualBus()
        ecu = VirtualEcu(
            bus,
            monitors=[Monitor("P0455", "evap", lambda conditions: True, permanent=True)],
        )
        tool = ScanTool(VirtualChannel(bus))
        ecu.advance_cycle()

        self.assertIn("P0455", tool.read_permanent())
        tool.clear()
        dtcs = tool.read_dtcs()
        self.assertEqual([dtc.code for dtc in dtcs], ["P0455"])
        self.assertTrue(dtcs[0].permanent)

    def test_vin_over_multiframe(self) -> None:
        bus = VirtualBus()
        VirtualEcu(bus)
        tool = ScanTool(VirtualChannel(bus))
        self.assertEqual(tool.read_vin(), "XTA211500M0000001")

    def test_clear_codes_returns_on_next_failure(self) -> None:
        bus = VirtualBus()
        ecu = VirtualEcu(
            bus,
            monitors=[Monitor("P0171", "lean", lambda conditions: conditions["ambient_c"] < 0)],
        )
        tool = ScanTool(VirtualChannel(bus))
        ecu.advance_cycle(ambient_c=-10)
        ecu.advance_cycle(ambient_c=-10)
        tool.clear()
        self.assertEqual(tool.read_dtcs(), [])
        ecu.advance_cycle(ambient_c=-10)
        self.assertEqual([dtc.code for dtc in tool.read_dtcs()], ["P0171"])

    def test_freeze_frame(self) -> None:
        bus = VirtualBus()
        ecu = VirtualEcu(
            bus,
            monitors=[Monitor("P0171", "lean", lambda conditions: True)],
        )
        tool = ScanTool(VirtualChannel(bus))
        ecu.advance_cycle(ambient_c=-8, battery_v=11.9, rpm=820)
        freeze = tool.read_freeze("P0171")
        self.assertAlmostEqual(freeze["ambient_c"], -8)
        self.assertAlmostEqual(freeze["battery_v"], 11.9)
        self.assertAlmostEqual(freeze["rpm"], 820)


class FallbackChannel:
    """Адаптер, который не умеет UDS 0x19, но отвечает на режимы OBD."""

    name = "fake-obd"

    def __init__(self, uds: bool = False) -> None:
        self.uds = uds
        self.log: list[bytes] = []

    def request(self, payload: bytes) -> bytes:
        self.log.append(payload)
        if payload == b"\x19\x02\xFF":
            if not self.uds:
                from obd_lab.scanner import NegativeResponse

                raise NegativeResponse(0x19, 0x11)
            return b"\x59\x02\xFF\x01\x71\xAF"
        if payload == b"\x03":
            return b"\x43\x01\x71"
        if payload == b"\x07":
            return b"\x47\x21\x87"
        if payload == b"\x0A":
            return b"\x4A\x04\x55"
        raise TimeoutError(f"нет ответа на {payload.hex(' ')}")

    def close(self) -> None:
        return


class ChannelTest(unittest.TestCase):
    def test_uds_failure_falls_back_to_obd(self) -> None:
        tool = ScanTool(FallbackChannel())
        dtcs = {dtc.code: dtc for dtc in tool.read_dtcs()}

        self.assertIn("P0171", dtcs)
        self.assertIn("P2187", dtcs)
        self.assertTrue(dtcs["P0171"].flags["confirmedDTC"])
        self.assertTrue(dtcs["P0171"].flags["warningIndicatorRequested"])
        self.assertTrue(dtcs["P2187"].flags["pendingDTC"])
        self.assertTrue(dtcs["P0455"].permanent)

    def test_uds_preferred_when_available(self) -> None:
        tool = ScanTool(FallbackChannel(uds=True))
        dtcs = tool.read_dtcs()
        self.assertEqual([dtc.code for dtc in dtcs], ["P0171"])
        self.assertEqual(dtcs[0].status, 0xAF)

    def test_missing_mode_0a_does_not_break_reading(self) -> None:
        class NoPermanent(FallbackChannel):
            def request(self, payload: bytes) -> bytes:
                if payload == b"\x0A":
                    from obd_lab.scanner import NegativeResponse

                    raise NegativeResponse(0x0A, 0x12)
                return super().request(payload)

        dtcs = ScanTool(NoPermanent(uds=True)).read_dtcs()
        self.assertEqual([dtc.code for dtc in dtcs], ["P0171"])


class Elm327ParseTest(unittest.TestCase):
    def _channel(self, response_id: int | None = None) -> object:
        from obd_lab.elm327 import Elm327Channel

        channel = object.__new__(Elm327Channel)
        channel.response_id = response_id
        return channel

    def test_headers_and_noise(self) -> None:
        text = (
            "SEARCHING...\r"
            "7E8 06 41 00 BE 3E B8 13\r"
            "\r>"
        )
        frames = self._channel()._parse(text, echo="0100")
        self.assertEqual(frames, [bytes.fromhex("064100BE3EB813")])

    def test_multiframe_uds(self) -> None:
        text = (
            "7E8 10 0B 59 02 FF 01 71 AF\r"
            "7E8 21 21 87 20 00 00 00 00\r"
            "\r>"
        )
        from obd_lab.isotp import decode

        frames = self._channel()._parse(text)
        payload = decode(frames)
        self.assertEqual(payload, bytes.fromhex("5902FF0171AF2187200000"))

    def test_response_id_selects_ecu(self) -> None:
        text = "7E8 03 7F 19 11\r7E9 03 59 02 FF\r>"
        channel = self._channel(response_id=0x7E9)
        self.assertEqual(channel._parse(text), [bytes.fromhex("035902FF")])

    def test_garbage_is_ignored(self) -> None:
        self.assertEqual(self._channel()._parse("NODATA\r\r>"), [])


class KnowledgeTest(unittest.TestCase):
    def test_curated_base_loads(self) -> None:
        base = KnowledgeBase()
        self.assertIn("P0171", base.codes)
        self.assertIn("P0016", base.codes)
        self.assertGreaterEqual(len(base.codes), 30)

    def test_explain_curated(self) -> None:
        text = KnowledgeBase().explain("P0171")
        self.assertIn("Подсос", text)
        self.assertIn("проверка", text)

    def test_reference_fallback(self) -> None:
        base = KnowledgeBase()
        if not base.reference.available:
            self.skipTest("OBDex не импортирован")
        text = base.explain("P2A00")
        self.assertIn("OBDex", text)

    def test_unknown_code_is_honest(self) -> None:
        text = KnowledgeBase().explain("Z9999")
        self.assertIn("Z9999", text)


if __name__ == "__main__":
    unittest.main()
