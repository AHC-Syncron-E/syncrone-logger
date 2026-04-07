from unittest.mock import MagicMock

import pytest

# Import main so we can patch its specific references
import main
from main import VentilatorWorker

# -------------------------------------------------------------------------
# REAL DATA PAYLOAD
# -------------------------------------------------------------------------
REAL_BREATH_DATA = """
BS, S:57832,
3.52, 5.14
4.13, 5.09
8.04, 5.10
BE
BS, S:57833,
3.70, 5.13
4.02, 5.10
7.78, 5.18
BE
"""


class MockSerialDevice:
    """
    A smart mock that simulates a physical serial device.
    """

    def __init__(self, port_name, data_source, exception_class):
        self.port = port_name
        self.clean_lines = [line.strip() for line in data_source.strip().split('\n')]
        self.data_bytes = ("\n".join(self.clean_lines) + "\n").encode('latin-1')
        self.position = 0
        self.is_open = True
        self.broken = False
        self.ExceptionClass = exception_class

    @property
    def in_waiting(self):
        if self.broken:
            return 1
        return 100

    def read(self, size=1):
        if self.broken:
            raise self.ExceptionClass("Hardware Device Removed")

        available = len(self.data_bytes)
        chunk_size = min(size, 50)

        start = self.position
        end = (self.position + chunk_size) % available

        if end > start:
            chunk = self.data_bytes[start:end]
        else:
            chunk = self.data_bytes[start:] + self.data_bytes[:end]

        self.position = end
        return chunk

    def write(self, data):
        if self.broken:
            raise self.ExceptionClass("Write Failed")
        return len(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def close(self):
        self.is_open = False


class TestReconnectionLogic:

    @pytest.fixture
    def worker(self, temp_db, mocker):
        # 1. Mock File System & System Calls
        mocker.patch('main.VentilatorWorker.setup_system')
        mocker.patch('main.VentilatorWorker.open_log_files')
        mocker.patch('main.VentilatorWorker.close_system')
        mocker.patch('main.VentilatorWorker.check_file_rotation')
        mocker.patch('main.VentilatorWorker.safe_write_file')

        # 2. Create Worker with Temp DB
        # UPDATED: Added "dummy.db" to satisfy new __init__ signature
        worker = VentilatorWorker("TEST_RECONNECT", "dummy.db")
        worker.db_manager = temp_db

        # 3. TIMING ADJUSTMENT FOR TEST STABILITY
        # Give the worker plenty of time (10s) to survive the test's "unplugged" phase.
        # The worker sleeps 1s per loop, so 2s was too risky (race condition).
        worker.reconnect_timeout_seconds = 10.0
        worker.rx_throttle_interval = 0

        return worker

    def test_self_healing_workflow(self, worker, qtbot, mocker):
        """
        The "Boss Fight" Test.
        """

        # --- 1. DEFINE REAL EXCEPTION CLASS ---
        class RealSerialException(OSError):
            pass

        # Inject into the global mock so main.py can catch it
        main.serial.SerialException = RealSerialException

        # --- 2. SETUP DEVICE FACTORY ---
        active_devices = []

        def create_mock_device(port_name, *args, **kwargs):
            dev = MockSerialDevice(port_name, REAL_BREATH_DATA, RealSerialException)
            active_devices.append(dev)
            return dev

        # Directly configure the mock object that main.py is holding
        main.serial.Serial.side_effect = create_mock_device

        # --- 3. SETUP PORT DETECTION ---
        valid_ports = [
            MagicMock(device="COM3", vid=0x0403, pid=0x6001),
            MagicMock(device="COM4", vid=0x0403, pid=0x6001)
        ]

        main.serial.tools.list_ports.comports.return_value = valid_ports

        # --- 4. SAFETY MONITOR ---
        def on_worker_error(msg):
            pytest.fail(f"Worker crashed with: {msg}")

        worker.sig_error.connect(on_worker_error)

        # --- EXECUTION ---

        # Phase A: Startup
        with qtbot.waitSignal(worker.sig_breath_seq, timeout=5000) as blocker:
            worker.start()

        assert int(blocker.args[0]) >= 57832

        # Phase B: Disaster (Cable Pull)
        # 1. Break the I/O
        for dev in active_devices:
            dev.broken = True

        # 2. Make OS report 0 ports
        main.serial.tools.list_ports.comports.return_value = []

        with qtbot.waitSignal(worker.sig_connection_lost, timeout=2000):
            pass

        assert worker.port_a is None

        # Wait just 500ms.
        # This is enough for the worker to likely enter its first sleep(1.0).
        # We restore ports BEFORE the worker wakes up from that sleep or shortly after.
        qtbot.wait(500)

        # Phase C: Recovery (Plug back in)
        active_devices.clear()
        main.serial.tools.list_ports.comports.return_value = valid_ports

        # Expect restoration. The worker might be asleep for up to ~1000ms
        # from its own loop, so we wait up to 5000ms to be safe.
        with qtbot.waitSignal(worker.sig_connection_restored, timeout=5000):
            pass

        # Phase D: Resume
        with qtbot.waitSignal(worker.sig_breath_seq, timeout=5000) as blocker:
            pass

        assert int(blocker.args[0]) >= 57832

        worker.stop()
