"""Defect A: settings-frame field-count guard + mode-default handling.

The MISCF response to SNDF ends at Field 171 (PB840) or Field 173 (PB980)
right before <ETX>. The original guard ``len(parts) >= 173`` rejected every
PB840 frame, permanently pinning the mode to "Unknown" on that device.

These tests pin the relaxed guard (accept 171- and 173-field frames, reject
genuinely short/truncated ones) and the pure unknown-mode-warning decision.
"""

from unittest.mock import MagicMock

from main import MIN_SETTINGS_FIELDS, VentilatorWorker, should_warn_unknown_mode


def _make_frame(n_fields: int) -> str:
    """Build a synthetic MISCF frame that splits into exactly ``n_fields``
    comma-separated fields, with a valid mode at fields 7/8/9.

    Field 7 = mode ("A/C"), field 8 = mandatory ("VC"), field 9 = spont ("").
    Produces the display string "Mode: VC A/C".
    """
    fields = ["      "] * n_fields
    fields[0] = "MISCF"
    fields[7] = "A/C"
    fields[8] = "VC"
    fields[9] = ""
    return ",".join(fields) + "\r"


class TestFieldCountGuard:
    def test_min_fields_constant_is_171(self):
        # PB840 frames contain 171 fields; this is the minimum accepted.
        assert MIN_SETTINGS_FIELDS == 171

    def test_pb840_171_field_frame_accepted(self):
        frame = _make_frame(171)
        # Sanity: the frame really does split into 171 fields.
        assert len(frame.rstrip("\r").split(",")) == 171
        _buf, results = VentilatorWorker.parse_settings_chunk("", frame)
        assert results == ["Mode: VC A/C"]

    def test_pb980_173_field_frame_accepted(self):
        frame = _make_frame(173)
        assert len(frame.rstrip("\r").split(",")) == 173
        _buf, results = VentilatorWorker.parse_settings_chunk("", frame)
        assert results == ["Mode: VC A/C"]

    def test_170_field_frame_rejected(self):
        # One field short of a valid PB840 frame -> truncated, reject.
        frame = _make_frame(170)
        _buf, results = VentilatorWorker.parse_settings_chunk("", frame)
        assert results == []

    def test_short_packet_still_rejected(self):
        _buf, results = VentilatorWorker.parse_settings_chunk("", "MISCF,1,2,3\r")
        assert results == []


class TestUnknownModeWarning:
    """Pure decision helper for surfacing a permanent-Unknown session."""

    def test_unknown_triggers_warning(self):
        assert should_warn_unknown_mode("Unknown") is True

    def test_blank_triggers_warning(self):
        assert should_warn_unknown_mode("") is True
        assert should_warn_unknown_mode(None) is True
        assert should_warn_unknown_mode("   ") is True

    def test_real_mode_does_not_warn(self):
        assert should_warn_unknown_mode("VC A/C") is False
        assert should_warn_unknown_mode("PS SPONT") is False


class TestImmediateSettingsPoll:
    """On port identification the worker must request a settings frame (SNDF)
    immediately, instead of waiting the full 5 s cadence, to minimise the
    startup window recorded as "Unknown"."""

    def _worker(self, mocker, temp_db):
        mocker.patch("main.VentilatorWorker.setup_system")
        mocker.patch("main.VentilatorWorker.open_log_files")
        mocker.patch("main.VentilatorWorker.safe_write_file")
        w = VentilatorWorker("TEST_POLL", "dummy.db")
        w.db_manager = temp_db
        return w

    def test_assign_ports_polls_settings_immediately(self, mocker, temp_db):
        worker = self._worker(mocker, temp_db)
        wave_port = MagicMock(port="COM3")
        set_port = MagicMock(port="COM4")

        # Empty init buffer -> no waveform samples to persist.
        worker.assign_ports(wave_port, set_port, "", "A")

        set_port.write.assert_any_call(b"SNDF\r")

