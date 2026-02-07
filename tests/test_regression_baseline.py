import pytest
from syncrone.main import VentilatorWorker


class TestLegacyParserBehavior:
    """
    These tests verify the behavior of the EXISTING code in main.py.
    DO NOT change main.py until these tests pass.
    """

    @pytest.fixture
    def worker(self, mock_serial_ports, temp_db, mocker):
        # Prevent the worker from actually trying to create files/folders on disk
        mocker.patch('main.VentilatorWorker.setup_system')
        mocker.patch('main.VentilatorWorker.open_log_files')
        mocker.patch('main.VentilatorWorker.close_system')

        # Instantiate
        # UPDATED: Added "dummy.db" to satisfy new __init__ signature
        worker = VentilatorWorker("TEST_PATIENT", "dummy.db")

        # Inject the temp_db fixture so it doesn't crash on DB calls
        worker.db_manager = temp_db

        # Ensure buffer is empty
        worker.waveform_line_buffer = ""

        return worker

    def test_golden_master_parsing(self, worker):
        """
        Feeds a complex stream of data into the worker and ensures
        the exact correct signals come out.
        """
        # 1. Define the complex input
        input_sequence = [
            "10.5",  # Fragment 1
            ",20.1",  # Fragment 2
            "\n",  # Completes Line 1 -> Expect Signal(20.1, 10.5)
            "BS, S:500,\n",  # Breath Marker -> Expect Signal Breath(500)
            "GARBAGE\n",  # Junk -> Expect Nothing
            "30.0,40.0\n"  # Clean Line 2 -> Expect Signal(40.0, 30.0)
        ]

        # 2. Setup Capture Lists
        # We manually connect signals to lambdas that save the data
        received_waveforms = []
        received_breaths = []

        # Note: main.py emits (pressure, flow)
        worker.sig_waveform_data.connect(lambda p, f: received_waveforms.append([p, f]))
        worker.sig_breath_seq.connect(lambda s: received_breaths.append(s))

        # 3. Execution
        for chunk in input_sequence:
            worker.process_waveform_buffer(chunk)

        # 4. Assertions (The "Golden" behavior)

        # We expect exactly 2 waveform events and 1 breath event
        assert len(received_waveforms) == 2
        assert len(received_breaths) == 1

        # Check Signal 1: Pressure=20.1, Flow=10.5
        #
        assert received_waveforms[0] == [20.1, 10.5]

        # Check Signal 2: Breath Sequence "500"
        assert received_breaths[0] == "500"

        # Check Signal 3: Pressure=40.0, Flow=30.0
        assert received_waveforms[1] == [40.0, 30.0]

    def test_buffer_overflow_regression(self, worker):
        """
        Verify the existing safety valve logic works.
        """
        # Fill buffer past limit (8192)
        huge_chunk = "A" * 8200

        # Existing logic returns None on overflow
        result = worker.process_waveform_buffer(huge_chunk)

        # Buffer should be reset to empty string
        assert worker.waveform_line_buffer == ""
        assert result is None