"""H3: consecutive EDF-generation failures must surface to the UI.

Failures in generate_edf() were only appended to edf_error_log.txt while the
UI kept showing "RECORDING". ``update_edf_failure_state`` is the pure counter
that decides when to raise a one-shot alert after N consecutive failures; the
SnapshotWorker uses it to emit a Qt signal.
"""

import importlib.util
import sqlite3
from datetime import datetime, timedelta

import pytest

from main import EDF_FAILURE_ALERT_THRESHOLD, SnapshotWorker, update_edf_failure_state

edfio_missing = importlib.util.find_spec("edfio") is None


def test_success_resets_counter_and_never_alerts():
    count, alert = update_edf_failure_state(5, success=True)
    assert count == 0
    assert alert is False


def test_failures_below_threshold_do_not_alert():
    count = 0
    for _ in range(EDF_FAILURE_ALERT_THRESHOLD - 1):
        count, alert = update_edf_failure_state(count, success=False)
        assert alert is False
    assert count == EDF_FAILURE_ALERT_THRESHOLD - 1


def test_alert_fires_once_at_threshold():
    count = EDF_FAILURE_ALERT_THRESHOLD - 1
    count, alert = update_edf_failure_state(count, success=False)
    assert count == EDF_FAILURE_ALERT_THRESHOLD
    assert alert is True


def test_no_repeat_alert_past_threshold():
    count = EDF_FAILURE_ALERT_THRESHOLD
    count, alert = update_edf_failure_state(count, success=False)
    assert count == EDF_FAILURE_ALERT_THRESHOLD + 1
    assert alert is False


def test_recovery_then_new_burst_alerts_again():
    # A success between bursts resets, so a later burst can alert again.
    count, alert = update_edf_failure_state(EDF_FAILURE_ALERT_THRESHOLD + 2, success=True)
    assert (count, alert) == (0, False)
    for _ in range(EDF_FAILURE_ALERT_THRESHOLD - 1):
        count, alert = update_edf_failure_state(count, success=False)
        assert alert is False
    count, alert = update_edf_failure_state(count, success=False)
    assert alert is True


# --- generate_edf return-contract tests (wires the counter to reality) ---

def _seed(db_path, n):
    ts = (datetime.now() - timedelta(minutes=5)).isoformat()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE waveforms (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, raw_data TEXT,
            parsed_pressure REAL, parsed_flow REAL, vent_mode TEXT, breath_index INTEGER
        )
        """
    )
    conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, timestamp TEXT, raw_data TEXT)")
    for i in range(n):
        conn.execute(
            "INSERT INTO waveforms (timestamp, raw_data, parsed_pressure, parsed_flow, vent_mode, breath_index)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ts, None, 15.0 + (i % 8), -3.0 + (i % 5), "VC A/C", 1),
        )
    conn.commit()
    conn.close()


@pytest.mark.skipif(edfio_missing, reason="edfio library missing")
def test_generate_edf_returns_none_when_insufficient_data(tmp_path):
    db = tmp_path / "few.db"
    out = tmp_path / "out"
    out.mkdir()
    _seed(db, 10)  # < 50 (fs) -> not enough data
    assert SnapshotWorker(db, out, "PID1").generate_edf() is None


@pytest.mark.skipif(edfio_missing, reason="edfio library missing")
def test_generate_edf_returns_true_on_success(tmp_path):
    db = tmp_path / "ok.db"
    out = tmp_path / "out"
    out.mkdir()
    _seed(db, 60)
    assert SnapshotWorker(db, out, "PID1").generate_edf() is True


@pytest.mark.skipif(edfio_missing, reason="edfio library missing")
def test_generate_edf_returns_false_on_write_failure(tmp_path, mocker):
    db = tmp_path / "fail.db"
    out = tmp_path / "out"
    out.mkdir()
    _seed(db, 60)
    mocker.patch("main.Edf").return_value.write.side_effect = RuntimeError("disk full")
    assert SnapshotWorker(db, out, "PID1").generate_edf() is False
