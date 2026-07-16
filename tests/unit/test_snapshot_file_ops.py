"""C1: EDF snapshot file lifecycle (no cross-session data loss).

The original ``generate_edf`` deleted EVERY ``*.edf`` in the shared output
folder BEFORE writing the new one. That destroyed other sessions'/patients'
EDFs and could leave zero files if the subsequent write failed.

These tests pin the corrected lifecycle:
  * unrelated EDFs in the folder are never touched;
  * only THIS session's own previous snapshot is removed;
  * the predecessor is removed only AFTER a successful write;
  * a write failure never leaves the folder with zero EDFs.
"""

import importlib.util
import sqlite3
from datetime import datetime, timedelta

import pytest

from main import SnapshotWorker

edfio_missing = importlib.util.find_spec("edfio") is None
pytestmark = pytest.mark.skipif(edfio_missing, reason="edfio library missing")

# Recent timestamp within the 1-hour snapshot window and a valid EDF start
# date (edfio only accepts 1985-2084).
RECENT_TS = (datetime.now() - timedelta(minutes=5)).isoformat()


def _seed(db_path, n=60):
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
            (RECENT_TS, None, 15.0 + (i % 8), -3.0 + (i % 5), "VC A/C", 1 if i < 30 else 2),
        )
    conn.commit()
    conn.close()


def test_unrelated_edf_is_preserved(tmp_path):
    db_path = tmp_path / "s.db"
    out = tmp_path / "out"
    out.mkdir()
    _seed(db_path)

    foreign = out / "OTHER_PATIENT_20200101_000000_1H.edf"
    foreign.write_bytes(b"not a real edf but must survive")

    SnapshotWorker(db_path, out, "PID1").generate_edf()

    assert foreign.exists(), "an unrelated session's EDF was deleted"
    ours = list(out.glob("PID1_*.edf"))
    assert len(ours) == 1


def test_own_previous_snapshot_removed_after_new_write(tmp_path):
    db_path = tmp_path / "s.db"
    out = tmp_path / "out"
    out.mkdir()
    _seed(db_path)

    worker = SnapshotWorker(db_path, out, "PID1")

    # Simulate a prior snapshot from THIS session.
    prior = out / "PID1_20200101_000000_1H.edf"
    prior.write_bytes(b"old snapshot")
    worker._last_written_path = prior

    worker.generate_edf()

    assert not prior.exists(), "previous snapshot from this session was not cleaned up"
    ours = list(out.glob("PID1_*.edf"))
    assert len(ours) == 1


def test_write_failure_keeps_predecessor(tmp_path, mocker):
    db_path = tmp_path / "s.db"
    out = tmp_path / "out"
    out.mkdir()
    _seed(db_path)

    worker = SnapshotWorker(db_path, out, "PID1")

    good = out / "PID1_20200101_000000_1H.edf"
    good.write_bytes(b"previous good snapshot")
    worker._last_written_path = good

    # Force the EDF write to fail.
    mock_edf_cls = mocker.patch("main.Edf")
    mock_edf_cls.return_value.write.side_effect = RuntimeError("disk full")

    worker.generate_edf()

    assert good.exists(), "predecessor deleted despite failed write (data loss)"
    assert list(out.glob("~temp_*")) == [], "temp file left behind after failure"
