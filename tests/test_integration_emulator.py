#!/usr/bin/env python3
"""Интеграционные тесты против ELM327-emulator (тестовый стенд).

Запуск стенда (пакет ELM327-emulator, лицензия CC BY-NC-SA 4.0 —
только для тестов, в продукт не входит):

    python tools/run_elm_emulator.py 35000 car "0171,2187,0420"

Без запущенного эмулятора тесты пропускаются (skipUnless).

Особенности стенда, учтённые здесь и в obd_lab:
  * эмулятор шлёт блок 03/07/0A БЕЗ байта-счётчика (вопреки ISO 15765-4) —
    decode_dtc_block распознаёт это по чётной длине блока;
  * широковещательный 09 02 отвечает «тремя ЭБУ» по циклу — VIN меняется
    от запроса к запросу, поэтому проверяем только формат (17 символов);
  * UDS (19 02, 22 F1 90) не поддерживается — это именно то, что
    проверяет наш fallback на OBD-режимы;
  * эмулятор умирает от резкого разрыва соединения — закрываем
    socket.shutdown(SHUT_RDWR) перед close().
"""

from __future__ import annotations

import os
import socket
import unittest

_PORT_URL = os.environ.get("OBD_ELM_EMULATOR", "socket://127.0.0.1:35000")


def _emulator_reachable(url: str) -> bool:
    if not url.startswith("socket://"):
        return False
    host_port = url[len("socket://") :]
    host, _, port = host_port.partition(":")
    try:
        with socket.create_connection((host, int(port or 35000)), timeout=0.5):
            return True
    except OSError:
        return False


@unittest.skipUnless(
    _emulator_reachable(_PORT_URL), f"эмулятор не запущен ({_PORT_URL})"
)
class ElmEmulatorIntegrationTest(unittest.TestCase):
    """Один канал на весь класс: эмулятор капризен к соединениям."""

    channel = None

    @classmethod
    def setUpClass(cls) -> None:
        from obd_lab.elm327 import Elm327Channel

        cls.channel = Elm327Channel(_PORT_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.channel is not None:
            ser = getattr(cls.channel, "ser", None)
            sock = getattr(ser, "_socket", None)
            if sock is not None:
                try:
                    sock.shutdown(2)  # SHUT_RDWR — аккуратный разрыв
                except OSError:
                    pass
            cls.channel.close()

    def test_protocol_detected(self) -> None:
        self.assertIn("CAN", self.channel.protocol())

    def test_supported_pids(self) -> None:
        response = self.channel.request(b"\x01\x00")
        self.assertEqual(response[:2], b"\x41\x00")
        self.assertGreaterEqual(len(response), 6)

    def test_dtc_block_parses(self) -> None:
        from obd_lab.obd import decode_dtc_block
        from obd_lab.scanner import ScanTool

        tool = ScanTool(self.channel)
        codes = tool.read_confirmed_obd()
        # Декодер не должен ронять ни канонический CAN-блок, ни блок
        # эмулятора без счётчика; каждый код — валидная строка.
        for code in codes:
            self.assertRegex(code, r"^[PCBU][0-3][0-9A-F]{3}$")

    def test_vin_format(self) -> None:
        from obd_lab.scanner import ScanTool

        tool = ScanTool(self.channel)
        self.assertEqual(len(tool.read_vin()), 17)

    def test_uds_falls_back_to_obd(self) -> None:
        # Эмулятор не знает UDS: 19 02 даёт "NO DATA" -> TimeoutError,
        # read_dtcs должен молча уйти в OBD-режимы и что-то вернуть.
        from obd_lab.scanner import ScanTool

        tool = ScanTool(self.channel)
        for dtc in tool.read_dtcs():
            self.assertRegex(dtc.code, r"^[PCBU][0-3][0-9A-F]{3}$")


if __name__ == "__main__":
    unittest.main()
