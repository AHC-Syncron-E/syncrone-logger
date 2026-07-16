"""EDF-level tests for ventilation-mode annotations (Defect B fix).

These exercise ``SnapshotWorker.generate_edf`` end-to-end: seed a SQLite DB,
generate an EDF, read it back with edfio, and inspect the breath annotations.
They prove (1) VC+ is canonicalized to PCV in the written EDF, not VCV, and
(2) the raw composite mode is preserved in the file (ground truth not
silently destroyed).
"""

import importlib.util
import sqlite3
from datetime import datetime, timedelta

import pytest

from main import SnapshotWorker

edfio_missing = importlib.util.find_spec("edfio") is None
pytestmark = pytest.mark.skipif(edfio_missing, reason="edfio library missing")

# Recent timestamp within the 1-hour window and a valid EDF start date
# (edfio only accepts 1985-2084).
RECENT_TS = (datetime.now() - timedelta(minutes=5)).isoformat()


def _seed_two_breaths(db_path, vent_mode):
    """Create the waveforms/settings tables and seed two breaths (60 samples)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE waveforms (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT,
            raw_data        TEXT,
            parsed_pressure REAL,
            parsed_flow     REAL,
            vent_mode       TEXT,
            breath_index    INTEGER
        )
        """
    )
    conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, timestamp TEXT, raw_data TEXT)")
    # Two breaths so at least one mid-stream annotation plus the trailing one
    # are emitted. Timestamp far in the future so it survives the 1h cutoff.
    for n in range(60):
        breath_idx = 1 if n < 30 else 2
        pressure = 15.0 + (n % 10)  # vary so the EDF physical range is non-zero
        flow = -5.0 + (n % 7)
        conn.execute(
            """
            INSERT INTO waveforms
            (timestamp, raw_data, parsed_pressure, parsed_flow, vent_mode, breath_index)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (RECENT_TS, None, pressure, flow, vent_mode, breath_idx),
        )
    conn.commit()
    conn.close()


def _read_annotation_texts(output_folder):
    from edfio import read_edf

    files = list(output_folder.glob("*.edf"))
    assert len(files) == 1, f"expected exactly one EDF, found {files}"
    edf = read_edf(files[0])
    return [a.text for a in edf.annotations]


def test_vc_plus_written_as_pcv_not_vcv(tmp_path):
    """REGRESSION (Defect B): a VC+ recording must annotate as PCV in the EDF."""
    db_path = tmp_path / "vcplus.db"
    output_folder = tmp_path / "out"
    output_folder.mkdir()
    _seed_two_breaths(db_path, "VC+ A/C")

    SnapshotWorker(db_path, output_folder, "PID1").generate_edf()

    texts = _read_annotation_texts(output_folder)
    breath_modes = {t.split("-")[0] for t in texts if t and t[-1].isdigit() and "-" in t}
    assert "PCV" in breath_modes, f"expected PCV breath annotation, got {texts}"
    assert "VCV" not in breath_modes, f"VC+ was mislabelled as VCV: {texts}"


def test_raw_composite_mode_preserved_in_edf(tmp_path):
    """Ground truth: the raw 'VC+ A/C' string must survive into the EDF even
    though the breath annotation is canonicalized to PCV."""
    db_path = tmp_path / "raw.db"
    output_folder = tmp_path / "out"
    output_folder.mkdir()
    _seed_two_breaths(db_path, "VC+ A/C")

    SnapshotWorker(db_path, output_folder, "PID1").generate_edf()

    texts = _read_annotation_texts(output_folder)
    assert any("VC+ A/C" in t for t in texts), (
        f"raw composite mode was not preserved in EDF annotations: {texts}"
    )


def test_simv_mode_not_relabelled(tmp_path):
    """A SIMV composite must pass through, not be forced to VCV/PCV/PSV."""
    db_path = tmp_path / "simv.db"
    output_folder = tmp_path / "out"
    output_folder.mkdir()
    _seed_two_breaths(db_path, "VC+ PS SIMV")

    SnapshotWorker(db_path, output_folder, "PID1").generate_edf()

    texts = _read_annotation_texts(output_folder)
    breath_modes = {t.split("-")[0] for t in texts if t and t[-1].isdigit() and "-" in t}
    assert "VC+ PS SIMV" in breath_modes, f"SIMV composite altered: {texts}"
