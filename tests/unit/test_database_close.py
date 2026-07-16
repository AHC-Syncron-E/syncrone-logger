"""H1: DatabaseManager.close() must flush the pending batch.

Waveform rows are inserted with executemany() and committed only once per
second by the worker loop. sqlite3 opens an implicit transaction on the first
INSERT, so if close() runs without committing, the last (uncommitted) ~1 s of
samples is rolled back and permanently lost at session stop.
"""

import sqlite3

import main


def test_close_commits_pending_batch(tmp_path):
    db_file = tmp_path / "close.db"

    mgr = main.DatabaseManager(str(db_file))
    mgr.connect()

    rows = [
        ("sess", "2026-01-01T00:00:00", None, 1.0, 2.0, "VCV", 1),
        ("sess", "2026-01-01T00:00:01", None, 3.0, 4.0, "VCV", 1),
    ]
    # Insert but deliberately DO NOT call commit_batch() -- mirrors the state
    # at session stop when the last batch has not yet been flushed.
    mgr.insert_batch_waveforms(rows)

    mgr.close()

    # Reopen a fresh connection: rolled-back inserts would be gone.
    conn = sqlite3.connect(str(db_file))
    count = conn.execute("SELECT COUNT(*) FROM waveforms").fetchone()[0]
    conn.close()

    assert count == 2, "close() did not flush the pending batch (data loss)"
