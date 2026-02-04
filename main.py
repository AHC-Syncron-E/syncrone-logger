import sys
import time
import math
import ctypes
import re
import shutil
import sqlite3
import traceback
import os
import json
import code  # Required for the embedded console
from io import StringIO  # Required to capture print() output
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque

try:
    import psutil
    import wandb

    HAS_TELEMETRY_LIBS = True
except ImportError:
    HAS_TELEMETRY_LIBS = False

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
                               QGridLayout, QPlainTextEdit, QInputDialog)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer, QEvent
from PySide6.QtGui import QFont, QIcon, QColor, QCloseEvent, QPixmap, QMouseEvent, QTextCursor

# Graphing
import pyqtgraph as pg

# -----------------------------------------------------------------------------
# GLOBAL CONSTANTS
# -----------------------------------------------------------------------------
APP_VERSION = "1.3.1"  # Bumped version for batch-sample fix
DEBUG_PIN = "REDACTED_PIN"  # PIN required to access internal debugger


class TelemetryManager(QThread):
    """
    Monitors system resources and logs them to Weights & Biases.
    Runs in a separate thread to prevent blocking the UI or Serial comms.
    """

    def __init__(self, project_name="REDACTED_PROJECT"):
        super().__init__()
        self.project_name = project_name
        self.is_active = False  # Is WandB initialized?
        self.is_running = False  # Is the thread loop running?
        self.api_key = None

    def setup(self, api_key):
        """Authenticates and initializes the WandB run."""
        if not HAS_TELEMETRY_LIBS:
            return False

        if self.is_active:
            return True

        try:
            self.api_key = api_key
            wandb.login(key=self.api_key)

            # Create a unique run name: e.g., "sess_0128_1630_username"
            run_name = f"sess_{datetime.now().strftime('%m%d_%H%M')}_{os.getlogin()}"

            wandb.init(
                project=self.project_name,
                name=run_name,
                config={
                    "version": APP_VERSION,
                    "platform": sys.platform
                },
                # 'thread' mode is safer for GUI apps than 'process'
                settings=wandb.Settings(start_method="thread")
            )
            self.is_active = True
            return True
        except Exception as e:
            print(f"WandB Init Error: {e}")
            return False

    def run(self):
        """The main monitoring loop."""
        if not HAS_TELEMETRY_LIBS or not self.is_active: return

        process = psutil.Process(os.getpid())

        while self.is_running:
            try:
                # 1. Collect Metrics
                mem = process.memory_info()
                metrics = {
                    "app_rss_mb": mem.rss / (1024 * 1024),  # Resident Set Size (Physical RAM)
                    "app_vms_mb": mem.vms / (1024 * 1024),  # Virtual Memory Size
                    "app_cpu_percent": process.cpu_percent(interval=None),
                    "app_threads": process.num_threads(),
                    "system_cpu_percent": psutil.cpu_percent(),
                    "system_ram_percent": psutil.virtual_memory().percent
                }

                # 2. Push to WandB
                wandb.log(metrics)

                # 3. Sleep (Sample every 10 seconds)
                for _ in range(10):
                    if not self.is_running: break
                    time.sleep(1)

            except Exception as e:
                # If network fails, just wait and retry. Don't crash.
                time.sleep(5)

    def start_logging(self):
        if self.is_active:
            self.is_running = True
            self.start()

    def stop_logging(self):
        self.is_running = False
        self.wait()
        if self.is_active:
            try:
                wandb.finish()
            except:
                pass
            self.is_active = False


# -----------------------------------------------------------------------------
# 0. HELPER UI CLASSES
# -----------------------------------------------------------------------------
class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class EmbeddedTerminal(QDialog):
    """
    An in-process Python shell.
    Allows executing commands within the running application's context.
    Safe for AppLocker environments as it spawns no new processes.
    """

    def __init__(self, context_vars, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Syncron-E Internal Debugger")
        self.resize(800, 600)
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; }
            QPlainTextEdit { 
                background-color: #1e1e1e; 
                color: #00ff00; 
                font-family: Consolas, 'Courier New', monospace;
                font-size: 11pt;
                border: none;
            }
            QLineEdit {
                background-color: #2b2b2b;
                color: #ffffff;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 11pt;
                border-top: 1px solid #555;
                padding: 5px;
            }
        """)

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Output Display (Read-Only)
        self.display = QPlainTextEdit()
        self.display.setReadOnly(True)
        layout.addWidget(self.display)

        # Input Line
        self.input_line = QLineEdit()
        self.input_line.setPlaceholderText(">>> Type Python commands here...")
        self.input_line.returnPressed.connect(self.execute_command)
        layout.addWidget(self.input_line)

        # Python Console Logic
        self.console = code.InteractiveConsole(locals=context_vars)

        # Command History
        self.history = []
        self.history_idx = 0

        # Welcome Message
        self.write_output(f"--- SYNCRONE INTERNAL SHELL ---\nPython {sys.version}\n")
        self.write_output("Locals available: 'window', 'worker', 'serial', 'os', 'sys'\n")
        self.write_output("WARNING: This shell interacts directly with the live application.\n")
        self.write_output("-" * 40 + "\n")
        self.input_line.setFocus()

    def write_output(self, text):
        self.display.moveCursor(QTextCursor.End)
        self.display.insertPlainText(text)
        self.display.moveCursor(QTextCursor.End)

    def execute_command(self):
        cmd = self.input_line.text()
        self.write_output(f">>> {cmd}\n")
        self.history.append(cmd)
        self.history_idx = len(self.history)
        self.input_line.clear()

        if cmd.strip() == "clear":
            self.display.clear()
            return

        if cmd.strip() == "exit":
            self.accept()
            return

        # Redirect stdout/stderr to capture output
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        captured_output = StringIO()

        try:
            sys.stdout = captured_output
            sys.stderr = captured_output
            more = self.console.push(cmd)

            if more:
                self.write_output("... (multiline input not fully supported in this simple shell)\n")

        except Exception as e:
            captured_output.write(f"{e}\n")
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            output = captured_output.getvalue()
            if output:
                self.write_output(output)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Up:
            if self.history and self.history_idx > 0:
                self.history_idx -= 1
                self.input_line.setText(self.history[self.history_idx])
        elif event.key() == Qt.Key_Down:
            if self.history and self.history_idx < len(self.history) - 1:
                self.history_idx += 1
                self.input_line.setText(self.history[self.history_idx])
            else:
                self.history_idx = len(self.history)
                self.input_line.clear()

        super().keyPressEvent(event)


class AboutDialog(QDialog):
    def __init__(self, parent_window):
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

        self.btn_telemetry = QPushButton("Enable WandB Telemetry")
        self.btn_telemetry.setFixedHeight(40)
        self.btn_telemetry.setStyleSheet(
            "background-color: #333; color: #aaa; border: 1px solid #555; border-radius: 4px;")

        # Check state to update button appearance
        if not HAS_TELEMETRY_LIBS:
            self.btn_telemetry.setText("Telemetry Unavailable (Missing Libs)")
            self.btn_telemetry.setEnabled(False)
        elif self.parent_window.telemetry.is_active:
            self.btn_telemetry.setText("Telemetry ACTIVE ✓")
            self.btn_telemetry.setStyleSheet("background-color: #004400; color: #00ff00; border: 1px solid #00ff00;")
            self.btn_telemetry.setEnabled(False)
        else:
            self.btn_telemetry.clicked.connect(self.activate_telemetry)

        layout.addWidget(self.btn_telemetry)  # Add above Debugger button

        # Button
        self.btn_debug = QPushButton("Launch Internal Debugger")
        self.btn_debug.setFixedHeight(40)
        self.btn_debug.setStyleSheet("background-color: #444; color: #ccc; border: 1px solid #666; border-radius: 4px;")
        self.btn_debug.clicked.connect(self.launch_shell)

        # Adjust button style if recording to warn user
        if self.parent_window.is_logging:
            self.btn_debug.setText("Debugger (CAUTION: Recording Active)")
            self.btn_debug.setStyleSheet(
                "background-color: #552200; color: #ffa500; border: 1px solid #ffaa00; border-radius: 4px;")

        layout.addWidget(lbl_title)
        layout.addWidget(lbl_ver)
        layout.addWidget(lbl_info)
        layout.addStretch()
        layout.addWidget(self.btn_debug)
        layout.addWidget(QDialogButtonBox(QDialogButtonBox.Ok, accepted=self.accept))

    def activate_telemetry(self):
        # 1. Security Check
        text, ok = QInputDialog.getText(self, "Admin Access", "Enter PIN:", QLineEdit.Password)
        if not ok: return
        if text != DEBUG_PIN:  # REDACTED_PIN
            QMessageBox.warning(self, "Access Denied", "Incorrect PIN.")
            return

        # 2. Get API Key
        key, ok = QInputDialog.getText(self, "WandB Setup", "Enter WandB API Key:", QLineEdit.Password)
        if ok and key:
            self.btn_telemetry.setText("Connecting...")
            self.btn_telemetry.repaint()

            # 3. Init Telemetry
            if self.parent_window.telemetry.setup(key):
                self.parent_window.telemetry.start_logging()
                self.accept()
                QMessageBox.information(self.parent_window, "Connected", "Telemetry is now streaming to WandB.")
            else:
                self.btn_telemetry.setText("Enable WandB Telemetry")
                QMessageBox.critical(self, "Error", "Connection Failed.\nCheck internet or API key.")

    def launch_shell(self):
        text, ok = QInputDialog.getText(self, "Restricted Access",
                                        "Enter Debug PIN:",
                                        QLineEdit.Password)

        if not ok:
            return  # User cancelled

        if text != DEBUG_PIN:
            QMessageBox.warning(self, "Access Denied", "Incorrect PIN.")
            return

        context = {
            'window': self.parent_window,
            'worker': self.parent_window.worker,
            'serial': serial,
            'sys': sys,
            'os': os,
            'sqlite3': sqlite3,
            'db_manager': self.parent_window.worker.db_manager if self.parent_window.worker else None
        }

        self.accept()
        console = EmbeddedTerminal(context, self.parent_window)
        console.exec()


# -----------------------------------------------------------------------------
# 1. DATABASE MANAGER
# -----------------------------------------------------------------------------
class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.conn = None
        self.cursor = None

    def connect(self):
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

    def _needs_migration(self):
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

    def _get_tables(self, path):
        try:
            temp_conn = sqlite3.connect(str(path))
            cursor = temp_conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall()]
            temp_conn.close()
            return tables
        except:
            return []

    def _backup_and_reset(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = self.db_path.parent / f"syncrone_backup_SCHEMA_{timestamp}.db"
        try:
            shutil.move(str(self.db_path), str(backup_name))
        except:
            pass

    def _create_tables(self):
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
    def insert_batch_waveforms(self, rows):
        """
        Inserts multiple waveform samples at once for high fidelity.
        rows: List of tuples (session_id, timestamp, raw_data, pressure, flow, mode, breath_idx)
        """
        self.conn.executemany(
            "INSERT INTO waveforms (session_id, timestamp, raw_data, parsed_pressure, parsed_flow, vent_mode, breath_index) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows
        )

    # Legacy method (kept for initial handshake or non-batch use)
    def insert_waveform(self, session_id, raw_data, pressure=None, flow=None, mode=None, breath_idx=None):
        ts = datetime.now().isoformat()
        self.conn.execute(
            "INSERT INTO waveforms (session_id, timestamp, raw_data, parsed_pressure, parsed_flow, vent_mode, breath_index) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, ts, raw_data, pressure, flow, mode, breath_idx)
        )

    def insert_setting(self, session_id, raw_data):
        ts = datetime.now().isoformat()
        self.conn.execute("INSERT INTO settings (session_id, timestamp, raw_data) VALUES (?, ?, ?)",
                          (session_id, ts, raw_data))
        self.conn.commit()

    def commit_batch(self):
        if self.conn: self.conn.commit()

    def close(self):
        if self.conn: self.conn.close()


# -----------------------------------------------------------------------------
# 2. MARKER MANAGEMENT (Synchronized)
# -----------------------------------------------------------------------------
class BreathMarker:
    def __init__(self, plot_item, seq_num, y_offset=0):
        self.plot_item = plot_item
        self.seq_num = seq_num
        self.x_pos = -0.02

        self.line = pg.InfiniteLine(pos=self.x_pos, angle=90, pen=pg.mkPen('#555', width=1, style=Qt.DashLine))
        self.text = pg.TextItem(text=f"#{seq_num}", anchor=(0, 1), color="#ffa500")
        self.text.setPos(self.x_pos, y_offset)

        self.plot_item.addItem(self.line)
        self.plot_item.addItem(self.text)

    def shift(self, distance):
        self.x_pos += distance
        self.line.setPos(self.x_pos)
        self.text.setPos(self.x_pos, self.text.y())
        return self.x_pos

    def destroy(self):
        try:
            self.plot_item.removeItem(self.line)
            self.plot_item.removeItem(self.text)
        except:
            pass


class BreathMarkerManager:
    def __init__(self, plot_item):
        self.plot_item = plot_item
        self.markers = {}

    def add_marker(self, seq_num, y_offset=0):
        if seq_num in self.markers: return
        try:
            marker = BreathMarker(self.plot_item, seq_num, y_offset)
            self.markers[seq_num] = marker
        except:
            pass

    def move_all(self, step_size):
        expired_ids = []
        for seq_num, marker in self.markers.items():
            new_x = marker.shift(step_size)
            if new_x < -10.0:
                expired_ids.append(seq_num)

        for seq_num in expired_ids:
            self.markers[seq_num].destroy()
            del self.markers[seq_num]


# -----------------------------------------------------------------------------
# 3. SNAPSHOT WORKER (UPDATED WITH MODE MAPPINGS)
# -----------------------------------------------------------------------------
class SnapshotWorker(QThread):
    def __init__(self, db_path, output_folder, patient_id):
        super().__init__()
        self.db_path = str(db_path)
        self.output_folder = output_folder
        self.patient_id = patient_id
        self.is_running = True

    def run(self):
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
                else:
                    pass
            except Exception as e:
                try:
                    with open(self.output_folder / "edf_error_log.txt", "a") as f:
                        f.write(f"[{datetime.now()}] EDF Gen Fail: {e}\n")
                except:
                    pass

    def generate_edf(self):
        now_dt = datetime.now()
        cutoff = (now_dt - timedelta(hours=1)).isoformat()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Query all samples
        query = """
                SELECT parsed_pressure, parsed_flow, vent_mode, breath_index
                FROM waveforms
                WHERE timestamp > ?
                ORDER BY id ASC \
                """
        cursor.execute(query, (cutoff,))
        rows = cursor.fetchall()
        conn.close()

        # Truncation: Ensure integer seconds of data
        fs = 50
        num_samples = len(rows)
        if num_samples < fs:
            return

        remainder = num_samples % fs
        if remainder > 0:
            rows = rows[:-remainder]

        # Prepare signals
        pressures = np.array([r[0] if r[0] is not None else 0.0 for r in rows], dtype=np.float32)
        flows = np.array([r[1] if r[1] is not None else 0.0 for r in rows], dtype=np.float32)

        p_sig = EdfSignal(pressures, sampling_frequency=fs, label="Pressure", physical_dimension="cmH2O")
        f_sig = EdfSignal(flows, sampling_frequency=fs, label="Flow", physical_dimension="L/min")

        # --- MODE STANDARDIZATION LOGIC ---
        MODE_MAPPINGS = {
            "VC A/C": "VCV",
            "VC": "VCV",
            "VC+ A/C": "VCV",
            "VC+": "VCV",
            "PC A/C": "PCV",
            "PC": "PCV"
        }

        def get_clean_mode(raw_s):
            if not raw_s: return "Unknown"

            # 1. Check strict mapping first
            # We strip whitespace just in case, but keep case sensitivity
            # (or use .upper() if you expect casing variance)
            check_key = raw_s.strip()
            if check_key in MODE_MAPPINGS:
                return MODE_MAPPINGS[check_key]

            # 2. Fallback to strict sanitizer
            # (Removes + / characters etc)
            return "".join(c for c in raw_s if c.isalnum() or c in " -_.")

        annotations = []
        last_idx = None

        for i, row in enumerate(rows):
            raw_mode = row[2] if row[2] else "Unknown"
            current_idx = row[3]

            if current_idx is not None and current_idx != last_idx:
                onset_sec = i / float(fs)

                # Use the new mapping function
                final_mode_str = get_clean_mode(raw_mode)
                text = f"{final_mode_str}-{current_idx}"

                annot = EdfAnnotation(onset=onset_sec, duration=None, text=text)
                annotations.append(annot)
                last_idx = current_idx

        # Build EDF
        if annotations:
            edf = Edf(signals=[p_sig, f_sig], annotations=annotations)
        else:
            edf = Edf(signals=[p_sig, f_sig])

        # Patient ID Sanitization
        clean_pid = self.patient_id.strip().replace(" ", "_")
        if not clean_pid: clean_pid = "X"

        edf.patient = Patient(name=clean_pid)

        start_time_obj = now_dt - timedelta(hours=1)
        edf.startdate = start_time_obj.date()
        edf.starttime = start_time_obj.time()

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
        except Exception:
            if temp_path.exists():
                os.remove(temp_path)

        del pressures
        del flows
        del rows
        del edf


# -----------------------------------------------------------------------------
# 4. VENTILATOR WORKER
# -----------------------------------------------------------------------------
class VentilatorWorker(QThread):
    sig_status_update = Signal(str, str)
    sig_settings_msg = Signal(str)
    sig_waveform_data = Signal(float, float)
    sig_breath_seq = Signal(str)
    sig_error = Signal(str)
    sig_rx_activity = Signal(str)
    sig_connection_lost = Signal()
    sig_connection_restored = Signal()

    def __init__(self, patient_id):
        super().__init__()
        self.patient_id = patient_id
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

    def open_log_files(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_file_date = datetime.now().date()

        wf_name = f"waveforms_{timestamp}.txt"
        st_name = f"settings_{timestamp}.txt"

        self.file_waveform = open(self.raw_data_folder / wf_name, 'a', encoding='utf-8', buffering=1)
        self.file_settings = open(self.raw_data_folder / st_name, 'a', encoding='utf-8', buffering=1)

    def log_unidentified_data(self, source_port, data):
        try:
            debug_file = self.logs_folder / "startup_debug_log.txt"
            with open(debug_file, "a", encoding='utf-8') as f:
                clean = data.replace('\n', '\\n').replace('\r', '\\r')
                ts = datetime.now().strftime("%H:%M:%S.%f")
                f.write(f"[{ts}] [{source_port}] {clean}\n")
        except:
            pass

    def check_file_rotation(self):
        now = time.monotonic()
        if now - self.last_rotation_check < 60: return
        self.last_rotation_check = now
        if datetime.now().date() > self.current_file_date:
            self.sig_status_update.emit("ROTATING FILES...", "#00aaff")
            if self.file_waveform: self.file_waveform.close()
            if self.file_settings: self.file_settings.close()
            self.open_log_files()
            self.sig_status_update.emit("RECORDING (Rotated)", "#00ff00")

    def safe_write_file(self, file_handle, data):
        if file_handle:
            try:
                file_handle.write(data)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            except:
                pass

    def setup_system(self):
        db_path = self.system_folder / "syncrone.db"
        self.db_manager = DatabaseManager(str(db_path))
        self.db_manager.connect()
        self.open_log_files()

    def close_system(self):
        if self.port_a and self.port_a.is_open: self.port_a.close()
        if self.port_b and self.port_b.is_open: self.port_b.close()

    def configure_port(self, port_obj, baud_rate):
        port_obj.baudrate = baud_rate
        port_obj.bytesize = serial.EIGHTBITS
        port_obj.parity = serial.PARITY_NONE
        port_obj.stopbits = serial.STOPBITS_ONE
        port_obj.reset_input_buffer()
        port_obj.reset_output_buffer()

    def get_valid_ports(self):
        valid_devices = []
        ports = serial.tools.list_ports.comports()
        for port in ports:
            for (vid, pid) in self.SUPPORTED_DEVICES:
                if port.vid == vid and port.pid == pid:
                    valid_devices.append(port.device)
                    break
        return sorted(list(set(valid_devices)))

    def perform_reconnect_procedure(self):
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

    def run(self):
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

    def assign_ports(self, wave_port, set_port, init_buffer, name):
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
    def handle_waveform(self, data):
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

    def handle_settings(self, data):
        self.safe_write_file(self.file_settings, data)
        self.db_manager.insert_setting(self.patient_id, data)
        self.process_settings_buffer(data)

    # --- NEW STATIC PARSER (PURE LOGIC) ---
    @staticmethod
    def parse_incoming_chunk(current_buffer, new_chunk, max_size=8192):
        """
        Pure logic: Manages the buffer and extracts valid events.
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
    def parse_settings_chunk(current_buffer, new_chunk, max_size=8192):
        """
        Pure logic: Handles the specific CR-delimited CSV format of the PB980.
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

    def process_settings_buffer(self, new_chunk):
        """
        Orchestrator for settings.
        """
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

    def log_crash(self, e):
        try:
            with open(self.logs_folder / "error_log.txt", "a") as f:
                f.write(f"\n[CRASH {datetime.now()}] {str(e)}\n{traceback.format_exc()}\n")
        except:
            pass

    def stop(self):
        self.is_running = False
        self.wait()


# -----------------------------------------------------------------------------
# 5. MAIN WINDOW
# -----------------------------------------------------------------------------
class VentilatorApp(QMainWindow):
    def __init__(self):
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

        if self.config_corrupt_msg:
            QTimer.singleShot(500, lambda: QMessageBox.warning(self, "Config Reset", self.config_corrupt_msg))

        # Initialize Telemetry Manager (Starts in dormant state)
        self.telemetry = TelemetryManager()

    def prevent_sleep(self):
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
        except:
            pass

    def load_config(self):
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

    def _process_options(self, raw_list):
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

    def log_debug(self, msg):
        try:
            with open(self.base_folder / "error_log.txt", "a") as f:
                f.write(f"[LOG {datetime.now()}] {msg}\n")
        except:
            pass

    def init_ui(self):
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
        self.p_markers = BreathMarkerManager(self.p_plot)

        self.plot_widget.nextRow()

        self.f_plot = self.plot_widget.addPlot(title="Flow (L/min)")
        self.f_plot.showGrid(x=True, y=True, alpha=0.3)
        self.f_curve = self.f_plot.plot(pen=pg.mkPen('#ffff00', width=2), connect="finite")
        self.f_markers = BreathMarkerManager(self.f_plot)

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
        self.input_id.setPlaceholderText("Enter Patient ID to Enable Recording...")
        self.input_id.setToolTip("Unique identifier for the patient session.")
        self.input_id.setStyleSheet("padding: 5px; font-size: 14px; color: white; border: 1px solid #555;")
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

    def show_about_dialog(self):
        # Modified to use the new interactive AboutDialog
        dlg = AboutDialog(self)
        dlg.exec()

    def closeEvent(self, event: QCloseEvent):
        # Ensure thread stops cleanly on exit
        if self.telemetry.isRunning():
            self.telemetry.stop_logging()
        if self.is_logging or self.is_locked:
            msg = "Recording in progress!" if self.is_logging else "App is LOCKED."
            QMessageBox.warning(self, "Cannot Close", f"{msg}\nPlease stop recording/unlock first.")
            event.ignore()
        else:
            event.accept()

    def toggle_lock(self):
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

    def check_input(self):
        if self.is_logging or self.is_locked: return
        if self.input_id.text().strip():
            self.btn_action.setEnabled(True)
            self.btn_action.setStyleSheet("background-color: #007acc; color: white; border-radius: 5px;")
            self.btn_action.setToolTip("Ready to Record")
        else:
            self.btn_action.setEnabled(False)
            self.btn_action.setStyleSheet("background-color: #444; color: #888; border-radius: 5px;")
            self.btn_action.setToolTip("You must enter a Patient ID before recording can begin.")

    def check_disk_space(self):
        try:
            return shutil.disk_usage(str(self.base_folder)).free
        except:
            return 0

    def handle_worker_error(self, msg):
        self.worker.stop()
        QMessageBox.critical(self, "Connection Error", msg)
        self.stop_logging_procedure("Error: " + msg)

    def toggle_logging(self):
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

            self.render_timer.start()

            self.lbl_started.setText("WAITING FOR DATA...")
            self.lbl_duration.setText("WAITING...")
            self.lbl_breaths.setText("0")

            self.input_id.setEnabled(False)
            self.combo_stop.setEnabled(False)
            self.btn_action.setText("STOP RECORDING")
            self.btn_action.setStyleSheet("background-color: #cc3300; color: white; border-radius: 5px;")

            # 1. Main Worker
            pid = self.input_id.text().strip()
            self.worker = VentilatorWorker(pid)
            self.worker.sig_status_update.connect(self.update_status)
            self.worker.sig_settings_msg.connect(self.update_mode_display)
            self.worker.sig_breath_seq.connect(self.update_breath_index)
            self.worker.sig_waveform_data.connect(self.update_plot)
            self.worker.sig_rx_activity.connect(self.on_rx_activity)
            self.worker.sig_error.connect(self.handle_worker_error)
            self.worker.sig_connection_lost.connect(self.on_connection_lost)
            self.worker.sig_connection_restored.connect(self.on_connection_restored)
            self.worker.start()

            # 2. Snapshot Worker
            db_path = self.worker.system_folder / "syncrone.db"
            self.snapshot_worker = SnapshotWorker(db_path, self.base_folder, pid)
            self.snapshot_worker.start()

            self.last_pkt_time = time.monotonic()
            self.is_in_silence = False
        else:
            self.stop_logging_procedure("User Request")

    def stop_logging_procedure(self, reason):
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
    def on_connection_lost(self):
        if self.is_reconnecting: return
        self.is_reconnecting = True

        if self.segment_start_time:
            now = datetime.now()
            delta = (now - self.segment_start_time).total_seconds()
            self.accumulated_duration += delta

        self.segment_start_time = None
        self.update_status("RECONNECTING...", "#ffa500")

    @Slot()
    def on_connection_restored(self):
        if not self.is_reconnecting: return
        self.is_reconnecting = False
        self.segment_start_time = datetime.now()
        self.update_status("RECORDING (Recovered)", "#00ff00")

    @Slot(str)
    def update_mode_display(self, text):
        if "Mode:" in text:
            parts = text.split("Mode:", 1)
            if len(parts) > 1:
                val = parts[1].strip()
                html = f"<html><head/><body><span style='font-weight:600; color:#00aaff;'>Mode:</span> <span style='font-weight:400; color:#ffffff;'>{val}</span></body></html>"
                self.mode_lbl.setText(html)
        else:
            self.mode_lbl.setText(text)

    @Slot(str, str)
    def update_status(self, msg, color):
        if "LOGGING" in msg: msg = msg.replace("LOGGING", "RECORDING")
        self.status_lbl.setText(msg)
        self.status_dot.setStyleSheet(f"color: {color};")

    @Slot(str)
    def update_breath_index(self, seq_num):
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
    def on_rx_activity(self, port_id):
        style = "background-color: #00ff00; border-radius: 8px; border: 1px solid #555;"
        if port_id == "A":
            self.led_a.setStyleSheet(style)
            self.led_a_timer.start(50)
        elif port_id == "B":
            self.led_b.setStyleSheet(style)
            self.led_b_timer.start(50)

    @Slot(float, float)
    def update_plot(self, p, f):
        marker_id = self.pending_seq_num
        self.pending_seq_num = None
        self.render_queue.append((p, f, marker_id))

        if self.is_in_silence:
            self.is_in_silence = False
            if not self.is_reconnecting:
                self.update_status("RECORDING", "#00ff00")

    def render_loop(self):
        """
        Optimized Pacer (50Hz) using NumPy for zero-allocation updates.
        """
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

    def update_ui_dashboard(self):
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