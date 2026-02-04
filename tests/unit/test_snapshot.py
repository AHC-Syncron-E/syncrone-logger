import pytest
import sqlite3
import os
from pathlib import Path
from main import SnapshotWorker


class TestSnapshotWorker:
    """
    Tests the background worker that exports recent data to EDF files.
    """

    @pytest.fixture
    def setup_environment(self, tmp_path):
        """Creates a real temporary SQLite DB and an output folder."""
        db_path = tmp_path / "test_snapshot.db"
        output_folder = tmp_path / "Output"
        output_folder.mkdir()

        # Seed the DB with tables matching the NEW Schema in main.py
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
                     CREATE TABLE waveforms
                     (
                         id              INTEGER PRIMARY KEY AUTOINCREMENT,
                         timestamp       TEXT,
                         raw_data        TEXT,
                         parsed_pressure REAL,
                         parsed_flow     REAL,
                         vent_mode       TEXT,
                         breath_index    INTEGER
                     )
                     """)
        conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, timestamp TEXT, raw_data TEXT)")
        conn.commit()
        conn.close()

        return db_path, output_folder

    def test_file_generation_logic_with_spaces(self, setup_environment):
        """
        Verify that generate_edf creates the correct .edf file AND sanitizes
        patient IDs containing spaces (which would otherwise crash edfio).
        """
        db_path, output_folder = setup_environment

        conn = sqlite3.connect(str(db_path))

        # Insert 50 samples (1 second at 50Hz) to satisfy edfio requirements
        for i in range(50):
            conn.execute("""
                         INSERT INTO waveforms
                         (timestamp, raw_data, parsed_pressure, parsed_flow, vent_mode, breath_index)
                         VALUES (?, ?, ?, ?, ?, ?)
                         """, ("9999-12-31T23:59:59", "DATA", 20.0, 10.0, "VC-AC", 100))

        conn.commit()
        conn.close()

        # Instantiate with a "dirty" ID containing spaces
        dirty_id = "TEST PATIENT ID"
        worker = SnapshotWorker(db_path, output_folder, dirty_id)

        # Run
        worker.generate_edf()

        # Assertions
        # Expect the file to be named with underscores: TEST_PATIENT_ID...
        generated_files = list(output_folder.glob("TEST_PATIENT_ID*.edf"))

        if len(generated_files) == 0:
            try:
                import edfio
            except ImportError:
                pytest.skip("EDF file not generated (edfio library missing)")

        assert len(generated_files) == 1
        assert generated_files[0].stat().st_size > 0

    def test_atomic_write_safety(self, setup_environment, mocker):
        """
        Verify that the worker writes to a temp file first, then renames it.
        """
        db_path, output_folder = setup_environment

        worker = SnapshotWorker(db_path, output_folder, "TEST_PATIENT")

        mock_rename = mocker.patch('os.rename')
        mock_edf_cls = mocker.patch('main.Edf')
        mock_edf_instance = mock_edf_cls.return_value

        def side_effect_create_file(path):
            with open(path, 'w') as f:
                f.write("DUMMY EDF CONTENT")

        mock_edf_instance.write.side_effect = side_effect_create_file

        # Seed 50 samples
        conn = sqlite3.connect(str(db_path))
        for _ in range(50):
            conn.execute("""
                         INSERT INTO waveforms
                         (timestamp, raw_data, parsed_pressure, parsed_flow, vent_mode, breath_index)
                         VALUES (?, ?, ?, ?, ?, ?)
                         """, ("9999-12-31T23:59:59", "DATA", 10, 10, "Mode", 1))
        conn.commit()
        conn.close()

        worker.generate_edf()

        assert mock_rename.call_count >= 1
        args, _ = mock_rename.call_args_list[0]
        src, dst = args

        assert "~temp_" in str(src)
        assert ".edf" in str(dst)