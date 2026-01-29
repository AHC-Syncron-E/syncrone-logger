import pytest
import sqlite3
import os
import time
from pathlib import Path
from main import DatabaseManager


class TestDatabaseIntegration:
    """
    Integration tests for the DatabaseManager.
    These tests use a REAL SQLite file (in a temp folder), not mocks.
    """

    def test_initialization_creates_schema(self, temp_db):
        """
        Verify that connecting to a fresh DB creates the correct tables.
        """
        # Connect to the underlying DB directly to verify schema
        cursor = temp_db.conn.cursor()

        # Check 'waveforms' table
        cursor.execute("PRAGMA table_info(waveforms)")
        columns = {row[1] for row in cursor.fetchall()}
        expected_cols = {'id', 'session_id', 'timestamp', 'raw_data', 'parsed_pressure', 'parsed_flow'}
        assert expected_cols.issubset(columns)

        # Check 'settings' table
        cursor.execute("PRAGMA table_info(settings)")
        columns = {row[1] for row in cursor.fetchall()}
        assert 'raw_data' in columns

    def test_waveform_insertion_and_retrieval(self, temp_db):
        """
        Critical Data Path: Insert data -> Commit -> Read back.
        """
        # 1. Insert Data
        session_id = "PATIENT_001"
        raw_line = "10.5,20.0"
        p_val = 20.0
        f_val = 10.5

        temp_db.insert_waveform(session_id, raw_line, p_val, f_val)

        # 2. Force Commit (Simulate the 1.0s timer in main.py)
        temp_db.commit_batch()

        # 3. Read Back using raw SQL
        cursor = temp_db.conn.execute("SELECT * FROM waveforms WHERE session_id=?", (session_id,))
        row = cursor.fetchone()

        assert row is not None
        # Indices based on CREATE TABLE order:
        # id(0), session_id(1), timestamp(2), raw_data(3), parsed_pressure(4), parsed_flow(5)
        assert row[1] == session_id
        assert row[3] == raw_line
        assert row[4] == p_val
        assert row[5] == f_val

    def test_high_volume_throughput(self, temp_db):
        """
        Stress Test: Insert 10,000 rows.
        Ensures WAL mode handles high-frequency writes without locking errors.
        """
        start_time = time.monotonic()

        # Simulate 10 seconds of data at 1000Hz (extreme case)
        for i in range(10000):
            temp_db.insert_waveform("STRESS_TEST", f"data_{i}", 1.0, 1.0)
            if i % 100 == 0:  # Commit every 100 rows
                temp_db.commit_batch()

        temp_db.commit_batch()
        duration = time.monotonic() - start_time

        # Verify count
        cursor = temp_db.conn.execute("SELECT COUNT(*) FROM waveforms WHERE session_id='STRESS_TEST'")
        count = cursor.fetchone()[0]

        assert count == 10000
        print(f"\nInserted 10k rows in {duration:.2f}s")

    def test_schema_migration_trigger(self, tmp_path):
        """
        Verify that opening an OLD database triggers a backup and reset.
        """
        # 1. Create a "Legacy" DB manually (simulate version 1.0)
        old_db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(old_db_path))
        # Old schema (missing parsed columns)
        conn.execute("CREATE TABLE waveforms (id INTEGER PRIMARY KEY, raw_data TEXT)")
        conn.commit()
        conn.close()

        # 2. Initialize Manager pointing to this old file
        manager = DatabaseManager(str(old_db_path))
        manager.connect()

        # 3. Assertions
        # It should have detected the mismatch and created a backup
        backups = list(tmp_path.glob("*backup*"))
        assert len(backups) == 1

        # The current DB should now have the NEW schema
        cursor = manager.conn.execute("PRAGMA table_info(waveforms)")
        cols = [row[1] for row in cursor.fetchall()]
        assert "parsed_pressure" in cols

        manager.close()