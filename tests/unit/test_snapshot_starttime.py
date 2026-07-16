"""C2: EDF start timestamp must reflect the first captured sample.

The original code set the EDF start to ``now - 1 hour`` unconditionally, so a
short (e.g. 10-minute) capture claimed to have started ~50 minutes before it
actually did. The start time must instead come from the first selected
waveform row's timestamp.
"""

import importlib.util
import sqlite3
from datetime import datetime, timedelta

import pytest

from main import SnapshotWorker

edfio_missing = importlib.util.find_spec("edfio") is None
pytestmark = pytest.mark.skipif(edfio_missing, reason="edfio library missing")


def _seed_from(db_path, first_ts, n=60):
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
        ts = (first_ts + timedelta(seconds=i * 0.02)).isoformat()
        conn.execute(
            "INSERT INTO waveforms (timestamp, raw_data, parsed_pressure, parsed_flow, vent_mode, breath_index)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ts, None, 15.0 + (i % 8), -3.0 + (i % 5), "VC A/C", 1 if i < 30 else 2),
        )
    conn.commit()
    conn.close()


def test_start_time_reflects_first_row_not_fixed_hour(tmp_path):
    from edfio import read_edf

    now = datetime.now()
    first = now - timedelta(minutes=10)

    db_path = tmp_path / "s.db"
    out = tmp_path / "out"
    out.mkdir()
    _seed_from(db_path, first)

    SnapshotWorker(db_path, out, "PID1").generate_edf()

    files = list(out.glob("PID1_*.edf"))
    assert len(files) == 1
    edf = read_edf(files[0])
    edf_start = datetime.combine(edf.startdate, edf.starttime)

    # Start must be ~10 min ago (the first sample), within EDF second-resolution.
    assert abs((edf_start - first).total_seconds()) < 5, (
        f"EDF start {edf_start} does not match first sample {first}"
    )
    # And it must NOT be the old fixed "now - 1 hour".
    old_wrong = now - timedelta(hours=1)
    assert abs((edf_start - old_wrong).total_seconds()) > 60, (
        "EDF start still pinned to now - 1 hour"
    )
