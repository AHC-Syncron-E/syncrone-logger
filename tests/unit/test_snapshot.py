import pytest
import sqlite3
import os
from pathlib import Path
from main import SnapshotWorker


class TestSnapshotWorker:
    """
    Tests the background worker that exports recent data to text files.
    """

    @pytest.fixture
    def setup_environment(self, tmp_path):
        """Creates a real temporary SQLite DB and an output folder."""
        db_path = tmp_path / "test_snapshot.db"
        output_folder = tmp_path / "Output"
        output_folder.mkdir()

        # Seed the DB with tables
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE waveforms (id INTEGER PRIMARY KEY, timestamp TEXT, raw_data TEXT)")
        conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, timestamp TEXT, raw_data TEXT)")
        conn.commit()
        conn.close()

        return db_path, output_folder

    def test_file_generation_logic(self, setup_environment):
        """Verify that generate_files creates the correct .txt files with content."""
        db_path, output_folder = setup_environment

        # Insert "Future" Data so it is definitely picked up by the "Last 1 Hour" query
        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO waveforms (timestamp, raw_data) VALUES (?, ?)",
                     ("9999-12-31T23:59:59", "FUTURE_WAVE_LINE\n"))
        conn.commit()
        conn.close()

        # Instantiate and Run Manually
        worker = SnapshotWorker(db_path, output_folder)
        worker.generate_files()

        # Assertions
        wf_file = output_folder / "LAST_1HOUR_WAVEFORMS.txt"
        st_file = output_folder / "LAST_1HOUR_SETTINGS.txt"

        assert wf_file.exists()
        assert st_file.exists()

        content = wf_file.read_text(encoding='utf-8')
        assert "FUTURE_WAVE_LINE" in content

    def test_atomic_write_safety(self, setup_environment, mocker):
        """
        Verify that the worker writes to a temp file first, then renames it.
        This prevents users from opening a half-written file.
        """
        db_path, output_folder = setup_environment
        worker = SnapshotWorker(db_path, output_folder)

        # Mock os.rename to verify it gets called
        mock_rename = mocker.patch('os.rename')

        # Seed Data
        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO waveforms (timestamp, raw_data) VALUES (?, ?)",
                     ("9999-12-31T23:59:59", "DATA"))
        conn.commit()
        conn.close()

        worker.generate_files()

        # Verify os.rename was called (temp -> final)
        assert mock_rename.call_count >= 1
        args, _ = mock_rename.call_args_list[0]
        src, dst = args
        assert "~temp_" in str(src)
        assert "LAST_1HOUR" in str(dst)