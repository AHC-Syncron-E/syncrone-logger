from __future__ import annotations

import sys
import os

# -----------------------------------------------------------------------------
# CRITICAL: MSIX/Nuitka "Zombie Pipe" Fix
# -----------------------------------------------------------------------------
# In "frozen" Windows apps (no console), C-level libraries (Qt, NumPy) write to
# file descriptors 1 (stdout) and 2 (stderr). Since no console reads them,
# the OS buffers this text in RAM indefinitely, causing a linear memory leak.
# We must redirect these FDs to "NUL" (the OS bit-bucket).
if getattr(sys, 'frozen', False):
    try:
        # 1. Silence Qt's internal chatty logging
        os.environ["QT_LOGGING_RULES"] = "*=false"

        # 2. Open the OS "Bit Bucket"
        null_fd = os.open("NUL", os.O_WRONLY)

        # 3. Force redirection of C-level File Descriptors (1=stdout, 2=stderr)
        os.dup2(null_fd, 1)
        os.dup2(null_fd, 2)

        # 4. Redirect Python-level objects
        sys.stdout = os.fdopen(1, 'w', buffering=1)
        sys.stderr = os.fdopen(2, 'w', buffering=1)
    except Exception:
        # Fallback for strict sandboxes
        try:
            sys.stdout = open(os.devnull, 'w')
            sys.stderr = open(os.devnull, 'w')
        except:
            pass

import time
import math
import ctypes
import re
import shutil
import sqlite3
import traceback
import json
import gc  # Required for explicit memory management in long-running threads
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque

# EDF and Math Libraries
import numpy as np

try:
    from edfio import Edf, EdfSignal, EdfAnnotation, Patient

    HAS_EDF_LIB = True
except ImportError:
    HAS_EDF_LIB = False
    print("CRITICAL WARNING: 'edfio' library not found. Snapshots will fail.")

# Serial Communication
import serial
import serial.tools.list_ports

# GUI Components
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QPushButton, QLabel, QFrame, QMessageBox,
                               QLineEdit, QComboBox, QSizePolicy, QDialog, QDialogButtonBox,
                               QGridLayout, QPlainTextEdit)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer, QEvent, QRegularExpression
from PySide6.QtGui import (QFont, QIcon, QColor, QCloseEvent, QPixmap,
                           QMouseEvent, QTextCursor, QPixmapCache, QRegularExpressionValidator)

# Graphing
import pyqtgraph as pg

# FORCE Software Rasterization (Safety against OpenGL leaks in MSIX)
pg.setConfigOption('useOpenGL', False)
pg.setConfigOption('enableExperimental', False)

# -----------------------------------------------------------------------------
# GLOBAL CONSTANTS
# -----------------------------------------------------------------------------
APP_VERSION = "1.4.2"  # Bumped for Memory Fixes

# -----------------------------------------------------------------------------
# 0. HELPER UI CLASSES
# -----------------------------------------------------------------------------
class ClickableLabel(QLabel):
    """QLabel subclass that emits a ``clicked`` signal on left-click."""
    clicked = Signal()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class AboutDialog(QDialog):
    """Modal dialog displaying application version and contact information."""

    def __init__(self, parent_window: QMainWindow) -> None:
        super().__init__(parent_window)
        self.parent_window = parent_window
        self.setWindowTitle("About Syncron-E")
        self.setFixedSize(400, 300)
        self.setStyleSheet("background-color: #2b2b2b; color: #ffffff;")

        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(30, 30, 30, 30)

        # Title
        lbl_title = QLabel("Syncron-E Waveform Recorder")
        lbl_title.setFont(QFont("Segoe UI", 16, QFont.Bold))
        lbl_title.setAlignment(Qt.AlignCenter)

        # Version
        lbl_ver = QLabel(f"Version {APP_VERSION}")
        lbl_ver.setStyleSheet("color: #aaa;")
        lbl_ver.setAlignment(Qt.AlignCenter)

        # Info
        lbl_info = QLabel("Autonomous Healthcare, Inc.\nsupport@autonomoushealthcare.com")
        lbl_info.setAlignment(Qt.AlignCenter)
        lbl_info.setStyleSheet("color: #ddd;")

        layout.addWidget(lbl_title)
        layout.addWidget(lbl_ver)
        layout.addWidget(lbl_info)
        layout.addStretch()
        layout.addWidget(QDialogButtonBox(QDialogButtonBox.Ok, accepted=self.accept))


# -----------------------------------------------------------------------------
# 1. DATABASE MANAGER
# -----------------------------------------------------------------------------
class DatabaseManager:
    """SQLite persistence layer for ventilator waveform and settings data.

    Uses WAL journal mode for concurrent read/write access during
    long-duration recording sessions. Schema includes indexed waveform
    and settings tables with automatic migration from older versions.

    Parameters
    ----------
    db_path : str | Path
        Filesystem path for the SQLite database file.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.conn = None
        self.cursor = None

    def connect(self) -> None:
        """Establish connection to the SQLite database and initialize schema."""
        if not self.db_path.parent.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        if self.db_path.exists():
            if self._needs_migration():
                self._backup_and_reset()

        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self._create_tables()

    def _needs_migration(self) -> bool:
        try:
            temp_conn = sqlite3.connect(str(self.db_path))
            cursor = temp_conn.cursor()
            cursor.execute("PRAGMA table_info(waveforms)")
            columns = [info[1] for info in cursor.fetchall()]
            temp_conn.close()
            if "waveforms" not in self._get_tables(self.db_path): return False

            # Check for new EDF columns
            if "vent_mode" not in columns or "breath_index" not in columns:
                return True
            return False
        except:
            return False

    def _get_tables(self, path: str | Path) -> list[str]:
        try:
            temp_conn = sqlite3.connect(str(path))
            cursor = temp_conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall()]
            temp_conn.close()
            return tables
        except:
            return []

    def _backup_and_reset(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = self.db_path.parent / f"syncrone_backup_SCHEMA_{timestamp}.db"
        try:
            shutil.move(str(self.db_path), str(backup_name))
        except:
            pass

    def _create_tables(self) -> None:
        # SCHEMA: one row per sample
        self.conn.execute("""
                          CREATE TABLE IF NOT EXISTS waveforms
                          (
                              id
                              INTEGER
                              PRIMARY
                              KEY
                              AUTOINCREMENT,
                              session_id
                              TEXT,
                              timestamp
                              TEXT,
                              raw_data
                              TEXT,
                              parsed_pressure
                              REAL,
                              parsed_flow
                              REAL,
                              vent_mode
                              TEXT,
                              breath_index
                              INTEGER
                          )
                          """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_wf_sess ON waveforms (session_id);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_wf_time ON waveforms (timestamp);")

        self.conn.execute("""
                          CREATE TABLE IF NOT EXISTS settings
                          (
                              id
                              INTEGER
                              PRIMARY
                              KEY
                              AUTOINCREMENT,
                              session_id
                              TEXT,
                              timestamp
                              TEXT,
                              raw_data
                              TEXT
                          )
                          """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_st_sess ON settings (session_id);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_st_time ON settings (timestamp);")
        self.conn.commit()

    # --- BATCH INSERT (NEW FIX) ---
    def insert_batch_waveforms(self, rows: list[tuple]) -> None:
        """Insert multiple waveform samples at once for high fidelity.

        Parameters
        ----------
        rows : list[tuple]
            List of tuples (session_id, timestamp, raw_data, pressure, flow, mode, breath_idx).
        """
        self.conn.executemany(
            "INSERT INTO waveforms (session_id, timestamp, raw_data, parsed_pressure, parsed_flow, vent_mode, breath_index) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows
        )

    # Legacy method (kept for initial handshake or non-batch use)
    def insert_waveform(self, session_id: str, raw_data: str, pressure: float | None = None, flow: float | None = None, mode: str | None = None, breath_idx: int | None = None) -> None:
        """Insert a raw waveform message into the waveforms table.

        Parameters
        ----------
        session_id : str
            Active recording session identifier.
        raw_data : str
            Raw CSV payload from the PB980 waveform port.
        pressure : float, optional
            Parsed pressure value.
        flow : float, optional
            Parsed flow value.
        mode : str, optional
            Ventilation mode from raw data.
        breath_idx : int, optional
            Breath index for segmentation.
        """
        ts = datetime.now().isoformat()
        self.conn.execute(
            "INSERT INTO waveforms (session_id, timestamp, raw_data, parsed_pressure, parsed_flow, vent_mode, breath_index) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, ts, raw_data, pressure, flow, mode, breath_idx)
        )

    def insert_setting(self, session_id: str, raw_data: str) -> None:
        """Insert a raw settings message into the settings table.

        Parameters
        ----------
        session_id : str
            Active recording session identifier.
        raw_data : str
            Raw CSV payload from the PB980 settings port.
        """
        ts = datetime.now().isoformat()
        self.conn.execute("INSERT INTO settings (session_id, timestamp, raw_data) VALUES (?, ?, ?)",
                          (session_id, ts, raw_data))
        self.conn.commit()

    def commit_batch(self) -> None:
        """Flush pending inserts to the database."""
        if self.conn: self.conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self.conn: self.conn.close()


class BreathMarkerPool:
    """
    Fixed-size pool of reusable breath markers.
    Eliminates create/destroy cycle that leaks under Nuitka+Shiboken.
    """
    POOL_SIZE = 20  # Max visible markers on screen at once

    def __init__(self, plot_item: pg.PlotItem) -> None:
        self.plot_item = plot_item
        self.pool = []
        self.active = {}  # seq_num -> pool_index

        # Pre-allocate all markers ONCE at startup
        for i in range(self.POOL_SIZE):
            line = pg.InfiniteLine(pos=-999, angle=90,
                                   pen=pg.mkPen('#555', width=1, style=Qt.DashLine))
            text = pg.TextItem(text="", anchor=(0, 1), color="#ffa500")
            text.setPos(-999, 0)

            # Add to scene but hide off-screen
            plot_item.addItem(line)
            plot_item.addItem(text)
            line.setVisible(False)
            text.setVisible(False)

            self.pool.append({
                'line': line,
                'text': text,
                'x_pos': -999,
                'in_use': False,
                'seq_num': None
            })

    def _get_free_slot(self) -> int:
        """Get an unused slot, or recycle the oldest active one."""
        for i, slot in enumerate(self.pool):
            if not slot['in_use']:
                return i

        # All slots in use — recycle the one furthest left (oldest)
        oldest_idx = None
        oldest_x = float('inf')
        for i, slot in enumerate(self.pool):
            if slot['in_use'] and slot['x_pos'] < oldest_x:
                oldest_x = slot['x_pos']
                oldest_idx = i

        if oldest_idx is not None:
            old_seq = self.pool[oldest_idx]['seq_num']
            if old_seq in self.active:
                del self.active[old_seq]
            self.pool[oldest_idx]['in_use'] = False
            self.pool[oldest_idx]['line'].setVisible(False)
            self.pool[oldest_idx]['text'].setVisible(False)
            return oldest_idx

        return 0  # Fallback

    def add_marker(self, seq_num: str, y_offset: float = 0) -> None:
        """Add a new breath marker to the pool.

        Parameters
        ----------
        seq_num : str
            Sequence number identifier for the breath marker.
        y_offset : float, optional
            Vertical offset for marker text placement (default: 0).
        """
        if seq_num in self.active:
            return

        idx = self._get_free_slot()
        slot = self.pool[idx]

        slot['x_pos'] = -0.02
        slot['in_use'] = True
        slot['seq_num'] = seq_num

        slot['line'].setPos(slot['x_pos'])
        slot['line'].setVisible(True)

        slot['text'].setText(f"#{seq_num}")
        slot['text'].setPos(slot['x_pos'], y_offset)
        slot['text'].setVisible(True)

        self.active[seq_num] = idx

    def move_all(self, step_size: float) -> None:
        """Move all active markers by a fixed step size and hide expired ones.

        Parameters
        ----------
        step_size : float
            Distance to translate all markers in the x-direction.
        """
        expired = []
        for seq_num, idx in self.active.items():
            slot = self.pool[idx]
            slot['x_pos'] += step_size
            slot['line'].setPos(slot['x_pos'])
            slot['text'].setPos(slot['x_pos'], slot['text'].y())

            if slot['x_pos'] < -10.0:
                expired.append(seq_num)

        for seq_num in expired:
            idx = self.active[seq_num]
            slot = self.pool[idx]
            # Just hide — never remove, never destroy
            slot['line'].setVisible(False)
            slot['text'].setVisible(False)
            slot['in_use'] = False
            slot['seq_num'] = None
            del self.active[seq_num]


# -----------------------------------------------------------------------------
# 3. SNAPSHOT WORKER (UPDATED WITH MODE MAPPINGS)
# -----------------------------------------------------------------------------
class SnapshotWorker(QThread):
    """Background thread that periodically exports waveform data to EDF+ files.

    Reads the most recent hour of waveform samples from the SQLite database,
    constructs EDF+ files with pressure/flow signals at 50 Hz and breath
    boundary annotations, and writes them atomically (temp file + rename)
    to the output folder.

    Parameters
    ----------
    db_path : str | Path
        Path to the SQLite waveform database.
    output_folder : Path
        Directory for generated EDF files.
    patient_id : str
        Patient identifier used in the EDF header and filename.
    """
    def __init__(self, db_path: str | Path, output_folder: Path, patient_id: str) -> None:
        super().__init__()
        self.db_path = str(db_path)
        self.output_folder = output_folder
        self.patient_id = patient_id
        self.is_running = True

    def run(self) -> None:
        """Execute the snapshot loop until stopped.

        Waits 10 seconds after start, then generates an EDF snapshot
        every ~2 minutes. Errors are logged to ``edf_error_log.txt``
        in the output folder rather than propagated.
        """
        time.sleep(10)
        while self.is_running:
            # Wait ~2 minutes
            for _ in range(120):
                if not self.is_running: return
                time.sleep(1)

            if not self.is_running: return

            try:
                if HAS_EDF_LIB:
                    self.generate_edf()
                    # FORCE CLEANUP after big allocation
                    gc.collect()
                else:
                    pass
            except Exception as e:
                try:
                    with open(self.output_folder / "edf_error_log.txt", "a") as f:
                        f.write(f"[{datetime.now()}] EDF Gen Fail: {e}\n")
                except:
                    pass

    def generate_edf(self) -> None:
        """Generate a single 1-hour EDF+ snapshot from the waveform database.

        Queries all waveform rows with timestamps within the last hour,
        maps them into pressure and flow numpy arrays at 50 Hz, attaches
        breath-boundary annotations with ventilation mode labels, and
        writes the result as an atomic temp-file-then-rename operation.

        The output file is named ``{PatientID}_{Timestamp}_1H.edf``.
        Any existing ``.edf`` files in the output folder are removed
        before the new file is written.
        """
        now_dt = datetime.now()
        cutoff = (now_dt - timedelta(hours=1)).isoformat()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 1. Count rows FIRST (Fast index scan)
        count_query = "SELECT COUNT(id) FROM waveforms WHERE timestamp > ?"
        cursor.execute(count_query, (cutoff,))
        count = cursor.fetchone()[0]

        fs = 50
        if count < fs:
            conn.close()
            return

        # 2. Pre-allocate Numpy Arrays
        p_arr = np.zeros(count, dtype=np.float32)
        f_arr = np.zeros(count, dtype=np.float32)

        # 3. Stream data directly into arrays
        query = """
                SELECT parsed_pressure, parsed_flow, vent_mode, breath_index
                FROM waveforms
                WHERE timestamp > ?
                ORDER BY id ASC
                """
        cursor.execute(query, (cutoff,))

        annotations = []

        # --- DURATION CALCULATION STATE VARIABLES ---
        active_breath_idx = None
        active_breath_onset = 0.0
        active_breath_count = 0
        active_mode_str = "Unknown"
        # --------------------------------------------

        # Use a counter for array indexing
        i = 0

        while True:
            # larger batch size for speed
            batch = cursor.fetchmany(10000)
            if not batch: break

            for row in batch:
                if i >= count: break  # Safety break

                # Direct assignment
                p_arr[i] = row[0] if row[0] is not None else 0.0
                f_arr[i] = row[1] if row[1] is not None else 0.0

                # Annotation Logic (Kept same)
                raw_mode = row[2] if row[2] else "Unknown"
                current_idx = row[3]

                if current_idx is not None:
                    # If the breath index has changed, we are transitioning:
                    # End of OLD breath -> Start of NEW breath
                    if current_idx != active_breath_idx:

                        # 1. Close out the PREVIOUS breath (if one existed)
                        if active_breath_idx is not None:
                            duration_sec = round(active_breath_count / float(fs), 2)
                            text = f"{active_mode_str}-{active_breath_idx}"

                            # We replace duration=None with the actual calculated seconds
                            annot = EdfAnnotation(
                                onset=active_breath_onset,
                                duration=duration_sec,
                                text=text
                            )
                            annotations.append(annot)

                        # 2. Initialize the NEW breath
                        active_breath_idx = current_idx
                        active_breath_onset = i / float(fs)
                        active_breath_count = 1 # Start counting samples for this new breath

                        # Parse the mode string once at the start of the breath
                        if raw_mode.strip() in ["VC A/C", "VC", "VC+ A/C", "VC+"]:
                            active_mode_str = "VCV"
                        elif raw_mode.strip() in ["PC A/C", "PC"]:
                            active_mode_str = "PCV"
                        else:
                            active_mode_str = "".join(c for c in raw_mode if c.isalnum() or c in " -_.")

                    else:
                        # Same breath index, just increment the sample counter
                        active_breath_count += 1

                i += 1

        conn.close()

        # --- EDGE CASE: Save the very last breath tracked ---
        if active_breath_idx is not None:
            duration_sec = round(active_breath_count / float(fs), 2)
            text = f"{active_mode_str}-{active_breath_idx}"
            annot = EdfAnnotation(
                onset=active_breath_onset,
                duration=duration_sec,
                text=text
            )
            annotations.append(annot)
        # ----------------------------------------------------

        # 4. Truncate if we fetched fewer rows than expected (or remainder)
        remainder = i % fs
        final_len = i - remainder

        # Slicing numpy arrays is cheap (views)
        if remainder > 0:
            p_arr = p_arr[:final_len]
            f_arr = f_arr[:final_len]
        elif i < count:
            p_arr = p_arr[:i]
            f_arr = f_arr[:i]

        # 5. Create Signals (p_arr and f_arr are already numpy)
        p_sig = EdfSignal(p_arr.astype(np.float64), sampling_frequency=fs, label="Pressure", physical_dimension="cmH2O")
        f_sig = EdfSignal(f_arr.astype(np.float64), sampling_frequency=fs, label="Flow", physical_dimension="L/min")

        # Build EDF
        if annotations:
            edf = Edf(signals=[p_sig, f_sig], annotations=annotations)
        else:
            edf = Edf(signals=[p_sig, f_sig])

        # Patient ID Sanitization
        clean_pid = "".join(c if (c.isalnum() or c in "-_") else "_" for c in self.patient_id)
        # Fallback if the ID becomes empty after cleaning
        if not clean_pid.strip():
            clean_pid = "Unknown_Patient"
        edf.patient = Patient(name=clean_pid)

        # Date/Time setup
        start_time_obj = now_dt - timedelta(hours=1)
        edf.startdate = start_time_obj.date()
        edf.starttime = start_time_obj.time()

        # File Operations
        file_ts = now_dt.strftime("%Y%m%d_%H%M%S")
        filename = f"{clean_pid}_{file_ts}_1H.edf"
        final_path = self.output_folder / filename
        temp_path = self.output_folder / f"~temp_{filename}.tmp"

        # Cleanup old files
        for existing_file in self.output_folder.glob("*.edf"):
            try:
                os.remove(existing_file)
            except OSError:
                pass

        try:
            edf.write(str(temp_path))
            if temp_path.exists():
                if final_path.exists():
                    os.remove(final_path)
                os.rename(temp_path, final_path)
        except Exception as e:
            try:
                with open(self.output_folder / "edf_error_log.txt", "a") as f:
                    f.write(f"[{datetime.now()}] Write Failed: {e}\n")
            except:
                pass

            if temp_path.exists():
                os.remove(temp_path)

        # Explicit cleanup
        del p_arr
        del f_arr
        gc.collect()


# -----------------------------------------------------------------------------
# 4. VENTILATOR WORKER
# -----------------------------------------------------------------------------
class VentilatorWorker(QThread):
    """Background thread for serial communication with PB980/PB840 ventilators.

    Manages dual serial port connections (waveform at 38400 bps, settings
    at 9600 bps), auto-identifies which port carries waveform vs. settings
    data, and emits parsed events via Qt signals. Includes automatic
    reconnection on cable disconnect with configurable timeout.

    Parameters
    ----------
    patient_id : str
        Patient identifier for this recording session.
    db_path : str | Path
        Path to the SQLite database for persisting waveform samples.

    Attributes
    ----------
    sig_waveform_data : Signal(float, float)
        Emitted for each parsed sample as ``(pressure, flow)``.
    sig_breath_seq : Signal(str)
        Emitted when a breath-start marker is detected, carrying the
        sequence number assigned by the ventilator.
    sig_settings_msg : Signal(str)
        Emitted with a formatted string when a settings payload is parsed.
    sig_status_update : Signal(str, str)
        Emitted as ``(message, hex_color)`` for UI status bar updates.
    sig_error : Signal(str)
        Emitted on fatal errors requiring the session to stop.
    sig_connection_lost : Signal
        Emitted when a serial disconnect is detected.
    sig_connection_restored : Signal
        Emitted after a successful automatic reconnection.
    """

    sig_status_update = Signal(str, str)
    sig_settings_msg = Signal(str)
    sig_waveform_data = Signal(float, float)
    sig_breath_seq = Signal(str)
    sig_error = Signal(str)
    sig_rx_activity = Signal(str)
    sig_connection_lost = Signal()
    sig_connection_restored = Signal()

    def __init__(self, patient_id: str, db_path: str | Path) -> None:
        super().__init__()
        self.patient_id = patient_id
        self.db_path = db_path
        self.is_running = False

        # CLINICAL STATE TRACKING (New additions)
        self.current_vent_mode = "Unknown"
        self.current_breath_index = 0

        # --- PATH LOGIC ---
        self.root_folder = Path.home() / "Desktop" / "Syncron-E Data"

        version_parts = APP_VERSION.split('.')
        storage_version = "1.0"
        if len(version_parts) >= 2:
            storage_version = f"{version_parts[0]}.{version_parts[1]}"

        self.system_folder = self.root_folder / ".syncrone_system" / f"v{storage_version}"
        self.logs_folder = self.system_folder / "logs"
        self.raw_data_folder = self.system_folder / "raw_data"

        self.root_folder.mkdir(parents=True, exist_ok=True)
        self.logs_folder.mkdir(parents=True, exist_ok=True)
        self.raw_data_folder.mkdir(parents=True, exist_ok=True)

        self.port_a = None
        self.port_b = None
        self.waveform_port = None
        self.settings_port = None

        self.file_waveform = None
        self.file_settings = None
        self.db_manager = None

        self.buffer_a = ""
        self.buffer_b = ""
        self.waveform_line_buffer = ""
        self.settings_line_buffer = ""
        self.MAX_BUFFER_SIZE = 8192
        self.last_rotation_check = 0
        self.current_file_date = None
        self.SUPPORTED_DEVICES = [(0x0403, 0x6001), (0x067B, 0x23A3), (0x067B, 0x2303)]

        self.waveform_pattern = re.compile(r"BS,\s*S:(\d+),")

        self.reconnect_timeout_seconds = 120

        self.last_rx_emit_a = 0
        self.last_rx_emit_b = 0
        self.rx_throttle_interval = 0.1

    def open_log_files(self) -> None:
        """Open timestamped raw-data log files for this recording segment."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_file_date = datetime.now().date()

        wf_name = f"waveforms_{timestamp}.txt"
        st_name = f"settings_{timestamp}.txt"

        self.file_waveform = open(self.raw_data_folder / wf_name, 'a', encoding='utf-8', buffering=1)
        self.file_settings = open(self.raw_data_folder / st_name, 'a', encoding='utf-8', buffering=1)

    def log_unidentified_data(self, source_port: str, data: str) -> None:
        """Log unidentified raw data during port initialization phase."""
        try:
            debug_file = self.logs_folder / "startup_debug_log.txt"
            with open(debug_file, "a", encoding='utf-8') as f:
                clean = data.replace('\n', '\\n').replace('\r', '\\r')
                ts = datetime.now().strftime("%H:%M:%S.%f")
                f.write(f"[{ts}] [{source_port}] {clean}\n")
        except:
            pass

    def check_file_rotation(self) -> None:
        """Check and handle daily log file rotation."""
        now = time.monotonic()
        if now - self.last_rotation_check < 60: return
        self.last_rotation_check = now
        if datetime.now().date() > self.current_file_date:
            self.sig_status_update.emit("ROTATING FILES...", "#00aaff")
            if self.file_waveform: self.file_waveform.close()
            if self.file_settings: self.file_settings.close()
            self.open_log_files()
            self.sig_status_update.emit("RECORDING (Rotated)", "#00ff00")

    def safe_write_file(self, file_handle: object, data: str) -> None:
        """Write data to file with explicit sync and error suppression."""
        if file_handle:
            try:
                file_handle.write(data)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            except:
                pass

    def setup_system(self) -> None:
        """Initialize database manager and open log files."""
        # CHANGED: Use the passed DB path, not the system one
        self.db_manager = DatabaseManager(str(self.db_path))
        self.db_manager.connect()
        self.open_log_files()

    def close_system(self) -> None:
        """Close all open serial ports."""
        if self.port_a and self.port_a.is_open: self.port_a.close()
        if self.port_b and self.port_b.is_open: self.port_b.close()

    def configure_port(self, port_obj: serial.Serial, baud_rate: int) -> None:
        """Configure serial port parameters."""
        port_obj.baudrate = baud_rate
        port_obj.bytesize = serial.EIGHTBITS
        port_obj.parity = serial.PARITY_NONE
        port_obj.stopbits = serial.STOPBITS_ONE
        port_obj.reset_input_buffer()
        port_obj.reset_output_buffer()

    def get_valid_ports(self) -> list[str]:
        """Scan system COM ports for supported USB-to-serial adapters.

        Returns
        -------
        list of str
            Sorted, deduplicated list of COM port device names (e.g.
            ``['COM3', 'COM4']``) matching known VID/PID pairs.
        """
        valid_devices = []
        ports = serial.tools.list_ports.comports()
        for port in ports:
            for (vid, pid) in self.SUPPORTED_DEVICES:
                if port.vid == vid and port.pid == pid:
                    valid_devices.append(port.device)
                    break
        return sorted(list(set(valid_devices)))

    def perform_reconnect_procedure(self) -> bool:
        """Attempt to re-establish serial connections after a disconnect.

        Closes existing ports and polls for two valid devices until
        the reconnect timeout expires.

        Returns
        -------
        bool
            True if reconnection succeeded, False if timeout elapsed.
        """
        self.sig_connection_lost.emit()
        self.sig_status_update.emit("CONNECTION LOST - RECONNECTING...", "#ffa500")
        try:
            if self.port_a: self.port_a.close()
            if self.port_b: self.port_b.close()
        except:
            pass
        self.port_a = None
        self.port_b = None
        self.start_wait = time.monotonic()

        while self.is_running:
            elapsed = time.monotonic() - self.start_wait
            remaining = self.reconnect_timeout_seconds - elapsed
            if remaining <= 0: return False
            self.sig_status_update.emit(f"RECONNECTING... ({int(remaining)}s)", "#ffa500")
            found = self.get_valid_ports()
            if len(found) == 2:
                try:
                    dev_a, dev_b = found[0], found[1]
                    self.port_a = serial.Serial(dev_a, timeout=0)
                    self.configure_port(self.port_a, 38400)
                    self.port_b = serial.Serial(dev_b, timeout=0)
                    self.configure_port(self.port_b, 38400)
                    return True
                except Exception as e:
                    pass
            time.sleep(1.0)
        return False

    def run(self) -> None:
        """Execute the main event loop for serial communication."""
        self.is_running = True
        try:
            self.sig_status_update.emit("SCANNING PORTS...", "#ffff00")
            found_devices = self.get_valid_ports()
            if len(found_devices) < 2:
                self.sig_error.emit(f"Found {len(found_devices)} cable(s).\nNeed exactly 2.")
                return

            dev_a, dev_b = found_devices[0], found_devices[1]
            self.setup_system()

            try:
                self.port_a = serial.Serial(dev_a, timeout=0)
                self.configure_port(self.port_a, 38400)
                self.port_b = serial.Serial(dev_b, timeout=0)
                self.configure_port(self.port_b, 38400)
            except Exception as e:
                self.sig_error.emit(f"Initial Connection Failed: {e}")
                return

            self.sig_status_update.emit("IDENTIFYING PORTS...", "#00aaff")

            start_time = time.monotonic()
            last_serial_write = start_time
            last_db_commit = start_time
            loop_interval = 0.004
            next_wake = time.monotonic() + loop_interval
            ports_identified = False

            while self.is_running:
                try:
                    now = time.monotonic()
                    self.check_file_rotation()

                    # --- PORT A ---
                    if self.port_a and self.port_a.in_waiting > 0:
                        data_a = self.port_a.read(self.port_a.in_waiting).decode('latin-1', errors='ignore')

                        if now - self.last_rx_emit_a > self.rx_throttle_interval:
                            self.sig_rx_activity.emit("A")
                            self.last_rx_emit_a = now

                        if not ports_identified:
                            self.log_unidentified_data("PORT_A", data_a)
                            self.buffer_a += data_a
                            if self.waveform_pattern.search(self.buffer_a):
                                self.assign_ports(self.port_a, self.port_b, self.buffer_a, "A")
                                ports_identified = True
                        else:
                            if self.port_a == self.waveform_port:
                                self.handle_waveform(data_a)
                            else:
                                self.handle_settings(data_a)

                    # --- PORT B ---
                    if self.port_b and self.port_b.in_waiting > 0:
                        data_b = self.port_b.read(self.port_b.in_waiting).decode('latin-1', errors='ignore')

                        if now - self.last_rx_emit_b > self.rx_throttle_interval:
                            self.sig_rx_activity.emit("B")
                            self.last_rx_emit_b = now

                        if not ports_identified:
                            self.log_unidentified_data("PORT_B", data_b)
                            self.buffer_b += data_b
                            if self.waveform_pattern.search(self.buffer_b):
                                self.assign_ports(self.port_b, self.port_a, self.buffer_b, "B")
                                ports_identified = True
                        else:
                            if self.port_b == self.waveform_port:
                                self.handle_waveform(data_b)
                            else:
                                self.handle_settings(data_b)

                    if now - last_db_commit >= 1.0:
                        self.db_manager.commit_batch()
                        last_db_commit = now

                    if ports_identified and (now - last_serial_write >= 5.0):
                        msg = "SNDF\r"
                        try:
                            self.settings_port.write(msg.encode('ascii'))
                            self.settings_port.flush()
                            last_serial_write = now
                        except:
                            pass

                    sleep_duration = next_wake - time.monotonic()
                    if sleep_duration > 0:
                        time.sleep(sleep_duration)
                    else:
                        next_wake = time.monotonic()
                    next_wake += loop_interval

                except (serial.SerialException, OSError):
                    success = self.perform_reconnect_procedure()
                    if success:
                        self.sig_connection_restored.emit()
                        ports_identified = False
                        self.buffer_a = ""
                        self.buffer_b = ""
                        continue
                    else:
                        self.sig_error.emit("Connection Lost. Reconnect Failed.")
                        break

        except Exception as e:
            self.log_crash(e)
            self.sig_error.emit(f"Runtime Error: {e}")
        finally:
            self.close_system()
            if self.db_manager: self.db_manager.close()
            if self.file_waveform: self.file_waveform.close()
            if self.file_settings: self.file_settings.close()

    def assign_ports(self, wave_port: serial.Serial, set_port: serial.Serial, init_buffer: str, name: str) -> None:
        """Assign waveform and settings serial ports and process initial buffer."""
        self.waveform_port = wave_port
        self.settings_port = set_port
        self.configure_port(self.settings_port, 9600)
        w_name = self.waveform_port.port
        s_name = self.settings_port.port
        self.sig_status_update.emit(f"RECORDING | Wave: {w_name} | Set: {s_name}", "#00ff00")

        # We manually process the init buffer with the new handler logic
        self.handle_waveform(init_buffer)

    # -------------------------------------------------------------------------
    # NEW FIX: HIGH FIDELITY STORAGE (ONE ROW PER SAMPLE)
    # -------------------------------------------------------------------------
    def handle_waveform(self, data: str) -> None:
        """Process incoming waveform serial data.

        Writes raw data to the text log file, parses it via
        `parse_incoming_chunk`, inserts parsed samples into the database
        with interpolated timestamps, and emits Qt signals for the
        real-time plot.

        Parameters
        ----------
        data : str
            Raw decoded bytes from the waveform serial port.
        """
        # 1. Write to text file (This is the immutable "Black Box" log)
        self.safe_write_file(self.file_waveform, data)

        # 2. Parse the chunk into events
        self.waveform_line_buffer, events = self.parse_incoming_chunk(
            self.waveform_line_buffer,
            data,
            self.MAX_BUFFER_SIZE
        )

        batch_data = []

        # Timestamp interpolation
        # We received this chunk 'now'. It likely contains ~5 samples (100ms).
        # To avoid staircase timestamps, we interpolate backwards.
        now = datetime.now()
        base_ts = now.timestamp()
        dt_step = 0.02  # Approx 50Hz

        # Count how many data points are in this chunk
        data_event_count = sum(1 for e in events if e[0] == 'DATA')
        data_idx = 0

        for event in events:
            evt_type = event[0]

            if evt_type == 'BREATH':
                seq_num = event[1]
                self.current_breath_index = int(seq_num)
                self.sig_breath_seq.emit(seq_num)

            elif evt_type == 'DATA':
                pressure, flow = event[1], event[2]

                # Interpolate timestamp: T_sample = T_base - (Samples_Left * dt)
                # This puts the last sample at 'now', and previous ones slightly in past
                offset = (data_event_count - 1 - data_idx) * dt_step
                sample_time = datetime.fromtimestamp(base_ts - offset).isoformat()

                # Prepare row for batch insert
                # raw_data is None because we have the .txt files
                row = (
                    self.patient_id,
                    sample_time,
                    None,
                    pressure,
                    flow,
                    self.current_vent_mode,
                    self.current_breath_index
                )
                batch_data.append(row)
                data_idx += 1

                # Emit latest point to GUI for real-time graph
                self.sig_waveform_data.emit(pressure, flow)

        # 3. Batch Insert into DB
        if batch_data:
            self.db_manager.insert_batch_waveforms(batch_data)

    def handle_settings(self, data: str) -> None:
        """Process incoming settings serial data."""
        self.safe_write_file(self.file_settings, data)
        self.db_manager.insert_setting(self.patient_id, data)
        self.process_settings_buffer(data)

    # --- NEW STATIC PARSER (PURE LOGIC) ---
    @staticmethod
    def parse_incoming_chunk(current_buffer: str, new_chunk: str, max_size: int = 8192) -> tuple[str, list[tuple[str, ...]]]:
        """Parse a chunk of serial data from the PB980 waveform port.

        Accumulates partial lines in a buffer and extracts complete
        waveform samples and breath markers as they arrive.

        Parameters
        ----------
        current_buffer : str
            Incomplete line fragment carried over from the previous call.
        new_chunk : str
            New bytes received from the serial port, decoded as ASCII.
        max_size : int, optional
            Maximum allowed buffer length before overflow reset (default 8192).

        Returns
        -------
        remaining_buffer : str
            Any incomplete trailing line to carry forward.
        events : list of tuple
            Parsed events, each one of:
            - ``('DATA', pressure: float, flow: float)``
            - ``('BREATH', sequence_number: str)``

        Notes
        -----
        Overflow protection: if the combined buffer exceeds *max_size*,
        returns ``("", [])`` to prevent unbounded memory growth from a
        misbehaving serial device.
        """
        # 1. Update Buffer
        full_buffer = current_buffer + new_chunk
        if len(full_buffer) > max_size:
            return "", []  # Overflow protection

        if '\n' not in full_buffer:
            return full_buffer, []

        # 2. Split into processable lines
        lines = full_buffer.split('\n')
        remaining_buffer = lines[-1]
        complete_lines = lines[:-1]

        events = []
        # Pattern matching
        breath_pattern = r"BS,\s*S:(\d+),"

        for line in complete_lines:
            clean = line.strip()
            if not clean: continue

            if clean.startswith("BS"):
                match = re.search(breath_pattern, clean)
                if match:
                    events.append(('BREATH', match.group(1)))
                continue

            if clean.startswith("BE"):
                continue

            try:
                parts = clean.split(',')
                if len(parts) == 2:
                    flow = float(parts[0])
                    pressure = float(parts[1])
                    events.append(('DATA', pressure, flow))
            except ValueError:
                pass

        return remaining_buffer, events

    @staticmethod
    def parse_settings_chunk(current_buffer: str, new_chunk: str, max_size: int = 8192) -> tuple[str, list[str]]:
        """Parse a chunk of serial data from the PB980 settings port.

        The settings port sends CR-delimited CSV rows with 173+ fields.
        This method extracts the ventilation mode string from fields 7-9.

        Parameters
        ----------
        current_buffer : str
            Incomplete line fragment from the previous call.
        new_chunk : str
            New bytes from the settings serial port.
        max_size : int, optional
            Maximum allowed buffer length (default 8192).

        Returns
        -------
        remaining_buffer : str
            Incomplete trailing data to carry forward.
        messages : list of str
            Formatted mode strings, e.g. ``"Mode: VC A/C VC"``.
        """
        full_buffer = current_buffer + new_chunk
        if len(full_buffer) > max_size:
            return "", []  # Overflow protection

        if '\r' not in full_buffer:
            return full_buffer, []

        lines = full_buffer.split('\r')
        remaining_buffer = lines[-1]
        complete_lines = lines[:-1]

        results = []
        for line in complete_lines:
            clean = line.strip()
            if not clean: continue

            try:
                parts = clean.split(',')
                if len(parts) >= 173:
                    mode = parts[7].strip()
                    mandatory = parts[8].strip()
                    spont = parts[9].strip()

                    display_str = f"Mode: {mandatory} {spont} {mode}"
                    display_str = display_str.replace("  ", " ")

                    results.append(display_str)
            except Exception:
                pass

        return remaining_buffer, results

    def process_settings_buffer(self, new_chunk: str) -> None:
        """Parse settings data and update current ventilation mode state."""
        self.settings_line_buffer, messages = self.parse_settings_chunk(
            self.settings_line_buffer,
            new_chunk,
            self.MAX_BUFFER_SIZE
        )

        for msg in messages:
            # NEW: Update State for DB/EDF
            if "Mode:" in msg:
                self.current_vent_mode = msg.replace("Mode:", "").strip()
            self.sig_settings_msg.emit(msg)

    def log_crash(self, e: Exception) -> None:
        """Log exception details to error log."""
        try:
            with open(self.logs_folder / "error_log.txt", "a") as f:
                f.write(f"\n[CRASH {datetime.now()}] {str(e)}\n{traceback.format_exc()}\n")
        except:
            pass

    def stop(self) -> None:
        """Signal the worker to stop and block until the thread exits."""
        self.is_running = False
        self.wait()


# -----------------------------------------------------------------------------
# 5. MAIN WINDOW
# -----------------------------------------------------------------------------
class VentilatorApp(QMainWindow):
    """Main application window for the Syncron-E Waveform Recorder.

    Provides real-time dual-channel waveform visualization (pressure and
    flow) via PyQtGraph, session management with configurable auto-stop
    rules, breath counting, disk-space monitoring, and an auto-lock safety
    feature. Coordinates a `VentilatorWorker` for serial capture and a
    `SnapshotWorker` for periodic EDF export.
    """
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Syncron-E Waveform Recorder")
        self.setStyleSheet("background-color: #1e1e1e; color: #ffffff;")

        # Application State
        self.is_logging = False
        self.is_locked = False
        self.has_data_started = False
        self.is_reconnecting = False

        # TIMING / DURATION LOGIC
        self.session_start_time = None
        self.segment_start_time = None
        self.accumulated_duration = 0.0

        self.session_breath_count = 0
        self.start_disk_space = 0

        # RENDER QUEUE & TIMING (Jitter Buffer)
        self.render_queue = deque(maxlen=2500)
        self.render_timer = QTimer()
        self.render_timer.setInterval(20)  # Target 20ms (50Hz)
        self.render_timer.timeout.connect(self.render_loop)

        # Drift & Latency Control Variables
        self.last_render_call = time.monotonic()
        self.fractional_samples = 0.0
        self.last_pkt_time = 0
        self.is_in_silence = False

        # Buffer Synchronization
        self.pending_seq_num = None
        self.is_buffering = True
        self.buffer_start_time = None
        self.TARGET_LATENCY = 0.5  # 500ms safety margin

        # Config State
        self.base_folder = Path.home() / "Desktop" / "Syncron-E Data"
        self.base_folder.mkdir(parents=True, exist_ok=True)

        self.config_corrupt_msg = None
        self.auto_stop_options = self.load_config()

        # Threads
        self.worker = None
        self.snapshot_worker = None

        # 1Hz UI Timer
        self.ui_timer = QTimer()
        self.ui_timer.setInterval(1000)
        self.ui_timer.timeout.connect(self.update_ui_dashboard)

        if Path("icon.ico").exists():
            self.setWindowIcon(QIcon("icon.ico"))
        self.prevent_sleep()
        self.init_ui()

        # NEW: Force Garbage Collection every 5 minutes to combat heap fragmentation
        # from the continuous creation/deletion of graphics items and snapshot tuples.
        self.gc_timer = QTimer(self)
        self.gc_timer.setInterval(5 * 60 * 1000)  # 5 Minutes
        self.gc_timer.timeout.connect(self.force_maintenance)
        self.gc_timer.start()

        # AUTO-LOCK LOGIC (NEW)
        self.inactivity_timer = QTimer(self)
        self.inactivity_timer.setInterval(5 * 60 * 1000)  # 5 Minutes
        self.inactivity_timer.timeout.connect(self.perform_auto_lock)
        self.inactivity_timer.start()

        # Install Global Event Filter to catch mouse/keyboard across the whole app
        QApplication.instance().installEventFilter(self)

        if self.config_corrupt_msg:
            QTimer.singleShot(500, lambda: QMessageBox.warning(self, "Config Reset", self.config_corrupt_msg))


    def force_maintenance(self) -> None:
        """Run periodic garbage collection and clear Qt pixmap caches."""
        try:
            # 1. Force Python to reclaim circular references
            gc.collect()

            # 2. Empty Qt's internal memory caches
            # CRITICAL FIX: Use QPixmapCache (static class), NOT QPixmap.cache() instance method
            QPixmapCache.clear()
        except Exception as e:
            # Swallow error to prevent timer from dying or leaking stack traces
            print(f"Maintenance Error: {e}")

    def prevent_sleep(self) -> None:
        """Prevent Windows from entering sleep mode during recording."""
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
        except:
            pass

    def load_config(self) -> list[dict]:
        config_path = self.base_folder / ".config.json"

        # Defaults with Explicit Units
        defaults = [
            {"label": "Manual Stop (Unlimited)", "type": "manual", "value": 0, "unit": "none"},
            {"label": "1 Hour", "type": "time", "value": 1, "unit": "hours"},
            {"label": "12 Hours", "type": "time", "value": 12, "unit": "hours"},
            {"label": "24 Hours", "type": "time", "value": 24, "unit": "hours"},
            {"label": "48 Hours", "type": "time", "value": 48, "unit": "hours"},
            {"label": "72 Hours", "type": "time", "value": 72, "unit": "hours"},
            {"label": "1 Week", "type": "time", "value": 7, "unit": "days"},
            {"label": "2000 Breaths", "type": "breaths", "value": 2000, "unit": "breaths"},
            {"label": "5000 Breaths", "type": "breaths", "value": 5000, "unit": "breaths"}
        ]

        def save_defaults():
            with open(config_path, "w") as f:
                json.dump({"options": defaults}, f, indent=2)

        if not config_path.exists():
            save_defaults()
            return self._process_options(defaults)

        try:
            with open(config_path, "r") as f:
                data = json.load(f)

            raw_options = data.get("options", [])
            if not isinstance(raw_options, list) or len(raw_options) == 0:
                raise ValueError("Options list is empty or invalid")

            return self._process_options(raw_options)

        except Exception as e:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            corrupt_name = f".config_CORRUPT_{ts}.json"
            shutil.move(str(config_path), str(self.base_folder / corrupt_name))
            save_defaults()

            self.config_corrupt_msg = (
                f"Your configuration file had errors and was reset.\n\n"
                f"Issue: {e}\n\n"
                f"The corrupted file was backed up as:\n{corrupt_name}"
            )
            return self._process_options(defaults)

    def _process_options(self, raw_list: list[dict]) -> list[dict]:
        processed = []
        for i, item in enumerate(raw_list):
            if not all(k in item for k in ("label", "type", "value", "unit")):
                raise ValueError(f"Item #{i + 1} missing required keys")

            p_item = item.copy()
            unit = str(item["unit"]).lower().strip()
            val = item["value"]

            if item["type"] == "time":
                if "second" in unit:
                    p_item["value"] = val
                elif "minute" in unit:
                    p_item["value"] = val * 60
                elif "hour" in unit:
                    p_item["value"] = val * 3600
                elif "day" in unit:
                    p_item["value"] = val * 86400
                elif "week" in unit:
                    p_item["value"] = val * 604800
                else:
                    raise ValueError(f"Unknown unit: {unit}")

            processed.append(p_item)
        return processed

    def log_debug(self, msg: str) -> None:
        """Append a debug message to the application error log."""
        try:
            with open(self.base_folder / "error_log.txt", "a") as f:
                f.write(f"[LOG {datetime.now()}] {msg}\n")
        except:
            pass

    def init_ui(self) -> None:
        """Build the main window layout, graph widgets, and footer controls."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Header Container
        header = QFrame()
        header.setStyleSheet("background-color: #333; border-radius: 8px;")

        # Grid Layout for Perfect Centering
        h_layout = QGridLayout(header)
        h_layout.setContentsMargins(15, 5, 15, 5)

        # --- WIDGET CREATION START ---
        self.status_dot = QLabel("●")
        self.status_dot.setFont(QFont("Arial", 28))
        self.status_dot.setStyleSheet("color: #888;")

        self.status_lbl = QLabel("READY")
        self.status_lbl.setFont(QFont("Segoe UI", 14, QFont.Bold))

        self.seq_lbl = QLabel()
        self.seq_lbl.setText(
            "<html><head/><body><span style='font-weight:600; color:#ffa500;'>Breath Index:</span> <span style='font-weight:400; color:#ffffff;'>--</span></body></html>")
        self.seq_lbl.setFont(QFont("Segoe UI", 14))
        self.seq_lbl.setStyleSheet("margin-left: 20px;")
        self.seq_lbl.setToolTip("The sequence number of the most recent breath, as assigned by the ventilator.")

        rx_font = QFont("Segoe UI", 10, QFont.Bold)
        rx_tip = "Flashes green when data is received on this cable connection."

        lbl_rx_a = QLabel("RX A:", font=rx_font)
        lbl_rx_a.setToolTip(rx_tip)
        self.led_a = QLabel()
        self.led_a.setFixedSize(16, 16)
        self.led_a.setStyleSheet("background-color: #111; border-radius: 8px; border: 1px solid #555;")
        self.led_a.setToolTip(rx_tip)
        self.led_a_timer = QTimer()
        self.led_a_timer.setSingleShot(True)
        self.led_a_timer.timeout.connect(
            lambda: self.led_a.setStyleSheet("background-color: #111; border-radius: 8px; border: 1px solid #555;"))

        lbl_rx_b = QLabel("RX B:", font=rx_font)
        lbl_rx_b.setToolTip(rx_tip)
        self.led_b = QLabel()
        self.led_b.setFixedSize(16, 16)
        self.led_b.setStyleSheet("background-color: #111; border-radius: 8px; border: 1px solid #555;")
        self.led_b.setToolTip(rx_tip)
        self.led_b_timer = QTimer()
        self.led_b_timer.setSingleShot(True)
        self.led_b_timer.timeout.connect(
            lambda: self.led_b.setStyleSheet("background-color: #111; border-radius: 8px; border: 1px solid #555;"))

        # Lock Button (Center)
        self.btn_lock = QPushButton("🔓 LOCK APP")
        self.btn_lock.setToolTip("Locks the interface to prevent accidental stopping or closing.")
        self.btn_lock.setFixedSize(140, 36)
        self.btn_lock.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.btn_lock.setStyleSheet("background-color: #007acc; color: white; border-radius: 4px; border: none;")
        self.btn_lock.clicked.connect(self.toggle_lock)

        self.mode_lbl = QLabel("Mode: --")
        self.mode_lbl.setFont(QFont("Segoe UI", 16))
        self.mode_lbl.setStyleSheet("color: #00aaff;")

        # Logo Logic
        self.logo_lbl = ClickableLabel()
        self.logo_lbl.setCursor(Qt.PointingHandCursor)
        self.logo_lbl.clicked.connect(self.show_about_dialog)

        if getattr(sys, 'frozen', False):
            base_path = Path(sys.executable).parent
        else:
            base_path = Path(__file__).parent

        logo_path = None
        svg_file = base_path / "ahc_logo.svg"
        png_file = base_path / "ahclogo.png"

        if svg_file.exists():
            logo_path = str(svg_file)
        elif png_file.exists():
            logo_path = str(png_file)

        # --- LAYOUT CONSTRUCTION ---
        # 1. Left Container
        left_container = QWidget()
        left_layout = QHBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(15)

        left_layout.addWidget(self.status_dot)
        left_layout.addWidget(self.status_lbl)
        left_layout.addWidget(self.seq_lbl)
        left_layout.addSpacing(25)
        left_layout.addWidget(lbl_rx_a)
        left_layout.addWidget(self.led_a)
        left_layout.addWidget(lbl_rx_b)
        left_layout.addWidget(self.led_b)
        left_layout.addStretch()

        # 2. Right Container
        right_container = QWidget()
        right_layout = QHBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)

        right_layout.addStretch()
        right_layout.addWidget(self.mode_lbl)

        if logo_path:
            pix = QPixmap(logo_path)
            if not pix.isNull():
                pix = pix.scaledToHeight(45, Qt.SmoothTransformation)
                self.logo_lbl.setPixmap(pix)
                right_layout.addSpacing(30)
                right_layout.addWidget(self.logo_lbl)
                right_layout.addSpacing(10)

        # 3. Add to Grid
        h_layout.addWidget(left_container, 0, 0)
        h_layout.addWidget(self.btn_lock, 0, 1, Qt.AlignCenter)
        h_layout.addWidget(right_container, 0, 2)

        # 4. Set Stretch for Centering
        h_layout.setColumnStretch(0, 1)
        h_layout.setColumnStretch(1, 0)
        h_layout.setColumnStretch(2, 1)

        # --- GRAPH SETUP ---
        pg.setConfigOption('background', '#000000')
        pg.setConfigOption('foreground', '#d0d0d0')
        pg.setConfigOptions(antialias=True)
        self.plot_widget = pg.GraphicsLayoutWidget()

        # --- FIX: NUMPY ARRAYS ---
        self.data_len = 500
        # Pre-allocate arrays (float32 is enough precision and saves 50% RAM vs float64)
        self.pressure_data = np.zeros(self.data_len, dtype=np.float32)
        self.flow_data = np.zeros(self.data_len, dtype=np.float32)
        # Pre-calculate X axis
        self.x_axis_data = np.linspace(-10, -0.02, self.data_len, dtype=np.float32)

        self.p_plot = self.plot_widget.addPlot(title="Pressure (cmH2O)")
        self.p_plot.showGrid(x=True, y=True, alpha=0.3)
        self.p_curve = self.p_plot.plot(pen=pg.mkPen('#00ff00', width=2), connect="finite")
        self.p_markers = BreathMarkerPool(self.p_plot)

        self.plot_widget.nextRow()

        self.f_plot = self.plot_widget.addPlot(title="Flow (L/min)")
        self.f_plot.showGrid(x=True, y=True, alpha=0.3)
        self.f_curve = self.f_plot.plot(pen=pg.mkPen('#ffff00', width=2), connect="finite")
        self.f_markers = BreathMarkerPool(self.f_plot)

        # Footer
        footer = QFrame()
        footer.setStyleSheet("background-color: #2b2b2b; border-radius: 5px;")
        f_main_layout = QVBoxLayout(footer)

        r1_layout = QHBoxLayout()

        # Input Group
        input_group = QWidget()
        ig_layout = QVBoxLayout(input_group)
        ig_layout.setContentsMargins(0, 0, 0, 0)
        ig_layout.setSpacing(2)
        lbl_id = QLabel("Patient ID:")
        lbl_id.setStyleSheet("color: #aaa; font-size: 13px; font-weight: bold;")
        self.input_id = QLineEdit()
        self.input_id.setPlaceholderText("Enter Patient ID (A-Z, 0-9, -, _) to Enable Recording...")
        self.input_id.setToolTip("Unique identifier. Allowed: Letters, Numbers, Dashes, Underscores.")
        self.input_id.setStyleSheet("padding: 5px; font-size: 14px; color: white; border: 1px solid #555;")

        regex = QRegularExpression("^[a-zA-Z0-9_-]*$")
        validator = QRegularExpressionValidator(regex, self.input_id)
        self.input_id.setValidator(validator)

        self.input_id.textChanged.connect(self.check_input)
        ig_layout.addWidget(lbl_id)
        ig_layout.addWidget(self.input_id)

        # Dropdown Group
        combo_group = QWidget()
        cg_layout = QVBoxLayout(combo_group)
        cg_layout.setContentsMargins(0, 0, 0, 0)
        cg_layout.setSpacing(2)
        lbl_auto = QLabel("Auto-Stop Rule:")
        lbl_auto.setStyleSheet("color: #aaa; font-size: 13px; font-weight: bold;")
        self.combo_stop = QComboBox()
        self.combo_stop.setToolTip("Automatically stop recording after a specific duration or breath count.")
        self.combo_stop.setStyleSheet(
            "padding: 5px; font-size: 14px; color: white; background: #333; border: 1px solid #555;")
        for opt in self.auto_stop_options: self.combo_stop.addItem(opt["label"], opt)
        cg_layout.addWidget(lbl_auto)
        cg_layout.addWidget(self.combo_stop)

        self.btn_action = QPushButton("START RECORDING")
        self.btn_action.setToolTip("You must enter a Patient ID before recording can begin.")
        self.btn_action.setMinimumHeight(60)
        self.btn_action.setFont(QFont("Segoe UI", 16, QFont.Bold))
        self.btn_action.setStyleSheet("background-color: #444; color: #888; border-radius: 5px;")
        self.btn_action.setEnabled(False)
        self.btn_action.clicked.connect(self.toggle_logging)

        # Layout Assembly (Lock button removed from here)
        r1_layout.addWidget(input_group, 2)
        r1_layout.addWidget(combo_group, 2)
        r1_layout.addWidget(self.btn_action, 4)

        r2_layout = QHBoxLayout()
        dash_font_lbl = QFont("Segoe UI", 10)
        dash_font_val = QFont("Segoe UI", 22, QFont.Bold)

        def make_dash_item(label, initial_val, tooltip, color="#ffffff"):
            w = QWidget()
            w.setToolTip(tooltip)
            vl = QVBoxLayout(w)
            vl.setSpacing(2)

            l = QLabel(label)
            l.setFont(dash_font_lbl)
            l.setStyleSheet("color: #aaa;")
            l.setFixedHeight(25)
            l.setAlignment(Qt.AlignBottom | Qt.AlignHCenter)

            v = QLabel(initial_val)
            v.setFont(dash_font_val)
            v.setStyleSheet(f"color: {color};")
            v.setAlignment(Qt.AlignTop | Qt.AlignHCenter)

            vl.addWidget(l)
            vl.addWidget(v)
            return w, v

        w_started, self.lbl_started = make_dash_item("STARTED:", "--",
                                                     "The exact date and time the first waveform data packet was received.",
                                                     "#ffffff")

        w_duration, self.lbl_duration = make_dash_item("DURATION:", "00:00:00",
                                                       "The total time elapsed since valid waveform data began recording.",
                                                       "#00e5ff")

        w_breaths, self.lbl_breaths = make_dash_item("BREATHS RECORDED:", "0",
                                                     "The total number of complete breaths captured during this recording session.",
                                                     "#ffa500")

        # Calculate initial disk space immediately
        initial_bytes = self.check_disk_space()
        gb = initial_bytes / (1024 ** 3)
        days = (initial_bytes / 5120) / 86400
        w_disk, self.lbl_disk = make_dash_item("DISK SPACE:", f"~{days:.1f} Days Free\n({gb:.0f} GB)",
                                               "Estimates how many days of recording are supported by your current free disk space.",
                                               "#ccff99")

        r2_layout.addWidget(w_started)
        r2_layout.addWidget(w_duration)
        r2_layout.addWidget(w_breaths)
        r2_layout.addWidget(w_disk)

        f_main_layout.addLayout(r1_layout)
        f_main_layout.addLayout(r2_layout)

        layout.addWidget(header, 1)
        layout.addWidget(self.plot_widget, 8)
        layout.addWidget(footer, 2)

    def show_about_dialog(self) -> None:
        """Display the About dialog."""
        # Modified to use the new interactive AboutDialog
        dlg = AboutDialog(self)
        dlg.exec()

    def closeEvent(self, event: QCloseEvent) -> None:
        # FIX: Removed reference to undefined self.telemetry
        if self.is_logging or self.is_locked:
            msg = "Recording in progress!" if self.is_logging else "App is LOCKED."
            QMessageBox.warning(self, "Cannot Close", f"{msg}\nPlease stop recording/unlock first.")
            event.ignore()
        else:
            event.accept()

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        """Detects user activity to reset the auto-lock timer."""
        if event.type() in (QEvent.MouseMove, QEvent.MouseButtonPress,
                            QEvent.KeyPress, QEvent.Wheel):
            # Reset timer if app is not already locked
            if not self.is_locked:
                self.inactivity_timer.start()

        return super().eventFilter(obj, event)

    def perform_auto_lock(self) -> None:
        """Called by timer. Only locks if currently unlocked."""
        if not self.is_locked:
            self.toggle_lock()
            # Optional: status update so user knows why it happened
            if self.is_logging:
                self.update_status("RECORDING (Auto-Locked)", "#00ff00")
            else:
                self.status_lbl.setText("READY (Auto-Locked)")

    def toggle_lock(self) -> None:
        """Toggle the UI lock state to prevent accidental interaction."""
        self.is_locked = not self.is_locked
        self.p_plot.getViewBox().setMouseEnabled(x=not self.is_locked, y=not self.is_locked)
        self.f_plot.getViewBox().setMouseEnabled(x=not self.is_locked, y=not self.is_locked)

        if self.is_locked:
            self.btn_lock.setText("🔒 UNLOCK")
            self.btn_lock.setStyleSheet("background-color: #cc3300; color: white; border-radius: 4px; border: none;")
            if self.is_logging:
                self.btn_action.setStyleSheet("background-color: #333; color: #666; border-radius: 5px;")
            self.btn_action.setEnabled(False)
            self.input_id.setEnabled(False)
            self.combo_stop.setEnabled(False)
        else:
            self.btn_lock.setText("🔒 LOCK APP")
            self.btn_lock.setStyleSheet("background-color: #007acc; color: white; border-radius: 4px; border: none;")
            if self.is_logging:
                self.btn_action.setStyleSheet("background-color: #cc3300; color: white; border-radius: 5px;")
                self.btn_action.setEnabled(True)
            else:
                self.check_input()
                self.input_id.setEnabled(True)
                self.combo_stop.setEnabled(True)

    def check_input(self) -> None:
        """Enable or disable the Start button based on patient ID input."""
        if self.is_logging or self.is_locked: return
        if self.input_id.text().strip():
            self.btn_action.setEnabled(True)
            self.btn_action.setStyleSheet("background-color: #007acc; color: white; border-radius: 5px;")
            self.btn_action.setToolTip("Ready to Record")
        else:
            self.btn_action.setEnabled(False)
            self.btn_action.setStyleSheet("background-color: #444; color: #888; border-radius: 5px;")
            self.btn_action.setToolTip("You must enter a Patient ID before recording can begin.")

    def check_disk_space(self) -> int:
        """Robust disk space check.

        If specific folder check fails, tries drive root.
        If both fail, returns a 'safe' large value (1TB) rather than blocking the user.
        """
        try:
            # 1. Try checking the specific data folder
            if not self.base_folder.exists():
                self.base_folder.mkdir(parents=True, exist_ok=True)
            return shutil.disk_usage(str(self.base_folder)).free
        except Exception:
            try:
                # 2. Fallback: Try checking the drive anchor (e.g., "C:\")
                return shutil.disk_usage(str(self.base_folder.anchor)).free
            except Exception:
                # 3. Failsafe: If OS refuses to report stats, assume 1TB free
                # so we don't block the user from recording.
                return 1024 * 1024 * 1024 * 1024

    def handle_worker_error(self, msg: str) -> None:
        """Handle a fatal error from the VentilatorWorker thread."""
        self.worker.stop()
        QMessageBox.critical(self, "Connection Error", msg)
        self.stop_logging_procedure("Error: " + msg)

    def toggle_logging(self) -> None:
        """Start or stop a recording session.

        When starting, validates disk space, creates a session-specific
        SQLite database, launches the VentilatorWorker and SnapshotWorker
        threads, and begins the render timer. When stopping, delegates
        to `stop_logging_procedure`.
        """
        if not self.is_logging:
            free_bytes = self.check_disk_space()
            if free_bytes < 500 * 1024 * 1024:
                QMessageBox.critical(self, "Disk Full", "Critically low disk space (<500MB).")
                return

            opt = self.combo_stop.currentData()
            if opt["type"] == "time":
                needed = opt["value"] * 5120 * 1.2
                if free_bytes < needed:
                    msg = f"Insufficient disk space for {opt['label']}.\nStart anyway?"
                    if QMessageBox.question(self, "Space Warning", msg,
                                            QMessageBox.Yes | QMessageBox.No) == QMessageBox.No: return

            self.is_logging = True
            self.has_data_started = False
            self.session_breath_count = 0
            self.start_disk_space = free_bytes

            # Reset Accumulator logic
            self.accumulated_duration = 0.0
            self.session_start_time = None
            self.segment_start_time = None

            # Reset Jitter Buffer
            # --- FIX: BOUNDED QUEUE (Safety Valve) ---
            self.render_queue = deque(maxlen=2500)

            self.fractional_samples = 0.0
            self.last_render_call = time.monotonic()

            # Reset Latency State
            self.pending_seq_num = None
            self.is_buffering = True
            self.buffer_start_time = None

            # ---------------------------------------------------------
            # NEW: Generate Session-Based Database Path
            # ---------------------------------------------------------
            pid = self.input_id.text().strip()

            # Sanitize PID for filename (remove invalid chars)
            clean_pid = "".join(c for c in pid if c.isalnum() or c in "-_")
            if not clean_pid: clean_pid = "Session"

            # Timestamp: YYYYMMDD_HHMMSS
            ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            db_filename = f"syncrone_{clean_pid}_{ts_str}.db"

            # Create a VISIBLE folder INSIDE the main data folder for these databases
            session_db_folder = self.base_folder / "Session_Databases"
            session_db_folder.mkdir(parents=True, exist_ok=True)

            full_db_path = session_db_folder / db_filename
            # ---------------------------------------------------------

            self.render_timer.start()

            self.lbl_started.setText("WAITING FOR DATA...")
            self.lbl_duration.setText("WAITING...")
            self.lbl_breaths.setText("0")

            self.input_id.setEnabled(False)
            self.combo_stop.setEnabled(False)
            self.btn_action.setText("STOP RECORDING")
            self.btn_action.setStyleSheet("background-color: #cc3300; color: white; border-radius: 5px;")

            # 1. Main Worker (Pass the new DB path)
            self.worker = VentilatorWorker(pid, full_db_path)
            self.worker.sig_status_update.connect(self.update_status)
            self.worker.sig_settings_msg.connect(self.update_mode_display)
            self.worker.sig_breath_seq.connect(self.update_breath_index)
            self.worker.sig_waveform_data.connect(self.update_plot)
            self.worker.sig_rx_activity.connect(self.on_rx_activity)
            self.worker.sig_error.connect(self.handle_worker_error)
            self.worker.sig_connection_lost.connect(self.on_connection_lost)
            self.worker.sig_connection_restored.connect(self.on_connection_restored)
            self.worker.start()

            # 2. Snapshot Worker (Pass the SAME DB path)
            self.snapshot_worker = SnapshotWorker(full_db_path, self.base_folder, pid)
            self.snapshot_worker.start()

            self.last_pkt_time = time.monotonic()
            self.is_in_silence = False
        else:
            self.stop_logging_procedure("User Request")

    def stop_logging_procedure(self, reason: str) -> None:
        """Tear down the active recording session.

        Parameters
        ----------
        reason : str
            Human-readable reason for stopping (e.g. "User Request",
            "Time Limit (24 Hours)", "CRITICAL LOW DISK").
        """
        if hasattr(self, 'worker') and self.worker:
            self.worker.stop()

        # Stop Snapshot Worker
        if hasattr(self, 'snapshot_worker') and self.snapshot_worker:
            self.snapshot_worker.is_running = False
            self.snapshot_worker.wait()
            self.snapshot_worker = None

        self.ui_timer.stop()
        self.render_timer.stop()

        self.is_logging = False
        self.input_id.setEnabled(not self.is_locked)
        self.combo_stop.setEnabled(not self.is_locked)
        self.btn_action.setText("START RECORDING")

        stop_time_str = datetime.now().strftime("%I:%M %p")

        if "Limit" in reason:
            self.update_status(f"COMPLETE at {stop_time_str} (Limit Reached)", "#00ff00")
        elif "Disk" in reason:
            self.update_status(f"STOPPED at {stop_time_str} (Low Disk)", "#ff0000")
        else:
            self.update_status(f"STOPPED at {stop_time_str} (User Request)", "#888888")

        self.log_debug(f"Stopped. Reason: {reason}")

        if not self.is_locked:
            self.check_input()
        else:
            self.btn_action.setStyleSheet("background-color: #333; color: #666;")

    # --- SELF HEALING HANDLERS ---
    @Slot()
    def on_connection_lost(self) -> None:
        if self.is_reconnecting: return
        self.is_reconnecting = True

        if self.segment_start_time:
            now = datetime.now()
            delta = (now - self.segment_start_time).total_seconds()
            self.accumulated_duration += delta

        self.segment_start_time = None
        self.update_status("RECONNECTING...", "#ffa500")

    @Slot()
    def on_connection_restored(self) -> None:
        if not self.is_reconnecting: return
        self.is_reconnecting = False
        self.segment_start_time = datetime.now()
        self.update_status("RECORDING (Recovered)", "#00ff00")

    @Slot(str)
    def update_mode_display(self, text: str) -> None:
        if "Mode:" in text:
            parts = text.split("Mode:", 1)
            if len(parts) > 1:
                val = parts[1].strip()
                html = f"<html><head/><body><span style='font-weight:600; color:#00aaff;'>Mode:</span> <span style='font-weight:400; color:#ffffff;'>{val}</span></body></html>"
                self.mode_lbl.setText(html)
        else:
            self.mode_lbl.setText(text)

    @Slot(str, str)
    def update_status(self, msg: str, color: str) -> None:
        if "LOGGING" in msg: msg = msg.replace("LOGGING", "RECORDING")
        self.status_lbl.setText(msg)
        self.status_dot.setStyleSheet(f"color: {color};")

    @Slot(str)
    def update_breath_index(self, seq_num: str) -> None:
        html = f"<html><head/><body><span style='font-weight:600; color:#ffa500;'>Breath Index:</span> <span style='font-weight:400; color:#ffffff;'>#{seq_num}</span></body></html>"
        self.seq_lbl.setText(html)

        self.pending_seq_num = seq_num

        if self.is_logging and self.has_data_started:
            self.session_breath_count += 1
            self.lbl_breaths.setText(f"{self.session_breath_count:,}")
            opt = self.combo_stop.currentData()
            if opt["type"] == "breaths" and self.session_breath_count >= opt["value"]:
                self.stop_logging_procedure(f"Breath Limit ({opt['value']})")

    @Slot(str)
    def on_rx_activity(self, port_id: str) -> None:
        style = "background-color: #00ff00; border-radius: 8px; border: 1px solid #555;"
        if port_id == "A":
            self.led_a.setStyleSheet(style)
            self.led_a_timer.start(50)
        elif port_id == "B":
            self.led_b.setStyleSheet(style)
            self.led_b_timer.start(50)

    @Slot(float, float)
    def update_plot(self, p: float, f: float) -> None:
        """Queue a waveform sample for the next render cycle.

        Parameters
        ----------
        p : float
            Pressure value in cmH2O.
        f : float
            Flow value in L/min.
        """
        marker_id = self.pending_seq_num
        self.pending_seq_num = None
        self.render_queue.append((p, f, marker_id))

        if self.is_in_silence:
            self.is_in_silence = False
            if not self.is_reconnecting:
                self.update_status("RECORDING", "#00ff00")

    def render_loop(self) -> None:
        """Optimized Pacer (50Hz) using NumPy for zero-allocation updates."""
        if not self.is_logging: return

        # 1. TIME DELTA CALCULATION
        now = time.monotonic()
        dt = now - self.last_render_call
        self.last_render_call = now

        # --- PHASE 1: PRE-ROLL BUFFERING ---
        if self.is_buffering:
            if len(self.render_queue) > 0:
                if self.buffer_start_time is None:
                    self.buffer_start_time = now

                # Check if we have buffered enough latency
                if (now - self.buffer_start_time) >= self.TARGET_LATENCY:
                    self.is_buffering = False
            return  # Wait until buffer is ready

        # --- PHASE 2: PLAYBACK ---
        self.fractional_samples += (dt * 50.0)
        count_to_pop = int(self.fractional_samples)
        self.fractional_samples -= count_to_pop

        # Queue Overflow Protection
        queue_len = len(self.render_queue)
        if queue_len > 100: count_to_pop += 1
        if queue_len > 300: count_to_pop += 5

        did_update = False

        for _ in range(count_to_pop):
            if self.render_queue:
                # -- A. HAS DATA --
                p, f, m_id = self.render_queue.popleft()

                # Optimized NumPy Roll (Shift left, append new at end)
                self.pressure_data[:-1] = self.pressure_data[1:]
                self.pressure_data[-1] = p

                self.flow_data[:-1] = self.flow_data[1:]
                self.flow_data[-1] = f

                if m_id:
                    self.p_markers.add_marker(m_id, y_offset=10)
                    self.f_markers.add_marker(m_id, y_offset=10)

                self.p_markers.move_all(-0.02)
                self.f_markers.move_all(-0.02)

                did_update = True

                if self.is_in_silence:
                    self.is_in_silence = False
                    self.update_status("RECORDING", "#00ff00")
            else:
                # -- B. STARVATION --
                self.pressure_data[:-1] = self.pressure_data[1:]
                self.pressure_data[-1] = np.nan

                self.flow_data[:-1] = self.flow_data[1:]
                self.flow_data[-1] = np.nan

                self.p_markers.move_all(-0.02)
                self.f_markers.move_all(-0.02)
                did_update = True

        # 3. REFRESH GRAPH
        if did_update:
            # Pass numpy arrays directly - ZERO list allocations!
            self.p_curve.setData(self.x_axis_data, self.pressure_data)
            self.f_curve.setData(self.x_axis_data, self.flow_data)

            # Check for prolonged silence (Alarm)
            if self.render_queue:
                self.last_pkt_time = now

            if (now - self.last_pkt_time) > 5.0:
                if not self.is_in_silence:
                    self.is_in_silence = True
                    self.update_status("SIGNAL LOST", "#ff0000")
                    self.is_buffering = True
                    self.buffer_start_time = None

            # UI Start Logic
            if not self.has_data_started and did_update:
                self.has_data_started = True
                self.session_start_time = datetime.now()
                self.segment_start_time = self.session_start_time
                self.lbl_started.setText(self.session_start_time.strftime("%b %d @ %I:%M %p"))
                self.ui_timer.start()

    def update_ui_dashboard(self) -> None:
        """Refresh the footer dashboard (duration, breath count, disk space).

        Called once per second by the UI timer while recording is active.
        Also enforces time-based auto-stop rules and disk-space limits.
        """
        if not self.is_logging or not self.has_data_started: return

        total_sec = self.accumulated_duration
        if self.segment_start_time and not self.is_reconnecting:
            total_sec += (datetime.now() - self.segment_start_time).total_seconds()

        m, s = divmod(int(total_sec), 60)
        h, m = divmod(m, 60)
        self.lbl_duration.setText(f"{h:02d}:{m:02d}:{s:02d}")

        opt = self.combo_stop.currentData()
        if opt["type"] == "time":
            if total_sec >= opt["value"]:
                self.stop_logging_procedure(f"Time Limit ({opt['label']})")
                return

        free_bytes = self.check_disk_space()
        if free_bytes < 500 * 1024 * 1024:
            self.stop_logging_procedure("CRITICAL LOW DISK")
            QMessageBox.critical(self, "Stopped", "Disk Space < 500MB.")
            return

        rate = 5120
        remaining_sec = free_bytes / rate
        days = remaining_sec / 86400
        gb = free_bytes / (1024 ** 3)
        self.lbl_disk.setText(f"~{days:.1f} Days Free\n({gb:.0f} GB)")


if __name__ == "__main__":
    def exception_hook(exctype, value, tb):
        error_msg = "".join(traceback.format_exception(exctype, value, tb))
        try:
            log_path = Path.home() / "Desktop" / "Syncron-E Data" / "error_log.txt"
            with open(log_path, "a") as f:
                f.write(f"\n[GUI CRASH {datetime.now()}]\n{error_msg}\n")
        except:
            pass
        sys.__excepthook__(exctype, value, tb)


    sys.excepthook = exception_hook
    app = QApplication(sys.argv)
    window = VentilatorApp()
    window.showMaximized()
    sys.exit(app.exec())