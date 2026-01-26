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
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque

# Serial Communication
import serial
import serial.tools.list_ports

# GUI Components
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QPushButton, QLabel, QFrame, QMessageBox,
                               QLineEdit, QComboBox, QSizePolicy, QDialog, QDialogButtonBox,
                               QGridLayout)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer, QEvent
from PySide6.QtGui import QFont, QIcon, QColor, QCloseEvent, QPixmap, QMouseEvent

# Graphing
import pyqtgraph as pg

# -----------------------------------------------------------------------------
# GLOBAL CONSTANTS
# -----------------------------------------------------------------------------
APP_VERSION = "1.2.1 (Stable)"


# -----------------------------------------------------------------------------
# 0. HELPER UI CLASSES
# -----------------------------------------------------------------------------
class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# -----------------------------------------------------------------------------
# 1. DATABASE MANAGER
# -----------------------------------------------------------------------------
class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.conn = None
        self.cursor = None

    def connect(self):
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
            if "parsed_pressure" not in columns: return True
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
        backup_name = self.db_path.parent / f"syncrone_backup_{timestamp}.db"
        try:
            shutil.move(str(self.db_path), str(backup_name))
        except:
            pass

    def _create_tables(self):
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
                              REAL
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

    def insert_waveform(self, session_id, raw_data, pressure=None, flow=None):
        ts = datetime.now().isoformat()
        self.conn.execute(
            "INSERT INTO waveforms (session_id, timestamp, raw_data, parsed_pressure, parsed_flow) VALUES (?, ?, ?, ?, ?)",
            (session_id, ts, raw_data, pressure, flow)
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
        # Align marker with the last data point on the x-axis (-0.02s)
        self.x_pos = -0.02

        self.line = pg.InfiniteLine(pos=self.x_pos, angle=90, pen=pg.mkPen('#555', width=1, style=Qt.DashLine))
        self.text = pg.TextItem(text=f"#{seq_num}", anchor=(0, 1), color="#ffa500")
        self.text.setPos(self.x_pos, y_offset)

        self.plot_item.addItem(self.line)
        self.plot_item.addItem(self.text)

    def shift(self, distance):
        """ Mechanically shifts the marker left/right by 'distance' """
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
        """
        Moves ALL markers by step_size.
        Call this EXACTLY once for every data point added to the graph.
        """
        expired_ids = []
        for seq_num, marker in self.markers.items():
            new_x = marker.shift(step_size)
            # Remove if it scrolls off the left side (-10.0 seconds)
            if new_x < -10.0:
                expired_ids.append(seq_num)

        for seq_num in expired_ids:
            self.markers[seq_num].destroy()
            del self.markers[seq_num]


# -----------------------------------------------------------------------------
# 3. SNAPSHOT WORKER
# -----------------------------------------------------------------------------
class SnapshotWorker(QThread):
    def __init__(self, db_path, output_folder):
        super().__init__()
        self.db_path = str(db_path)
        self.output_folder = output_folder
        self.is_running = True

    def run(self):
        time.sleep(10)
        while self.is_running:
            for _ in range(300):
                if not self.is_running: return
                time.sleep(1)

            if not self.is_running: return

            try:
                self.generate_files()
            except Exception:
                pass

    def generate_files(self):
        cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Waveforms
        cursor.execute("SELECT raw_data FROM waveforms WHERE timestamp > ? ORDER BY id ASC", (cutoff,))
        rows = cursor.fetchall()
        if rows:
            temp_name = self.output_folder / "~temp_last_1hr_wave.tmp"
            final_name = self.output_folder / "LAST_1HOUR_WAVEFORMS.txt"
            with open(temp_name, 'w', encoding='utf-8') as f:
                for row in rows: f.write(row[0])
            try:
                if final_name.exists(): os.remove(final_name)
                os.rename(temp_name, final_name)
            except OSError:
                if temp_name.exists(): os.remove(temp_name)

        # Settings
        cursor.execute("SELECT raw_data FROM settings WHERE timestamp > ? ORDER BY id ASC", (cutoff,))
        rows = cursor.fetchall()
        if rows:
            temp_name = self.output_folder / "~temp_last_1hr_settings.tmp"
            final_name = self.output_folder / "LAST_1HOUR_SETTINGS.txt"
            with open(temp_name, 'w', encoding='utf-8') as f:
                for row in rows: f.write(row[0])
            try:
                if final_name.exists(): os.remove(final_name)
                os.rename(temp_name, final_name)
            except OSError:
                if temp_name.exists(): os.remove(temp_name)
        conn.close()


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
        self.port_a = None
        self.port_b = None
        self.waveform_port = None
        self.settings_port = None
        self.base_folder = Path.home() / "Desktop" / "Syncron-E Data"
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

    def open_log_files(self):
        self.base_folder.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_file_date = datetime.now().date()
        wf_name = f"waveforms_{timestamp}.txt"
        st_name = f"settings_{timestamp}.txt"
        self.file_waveform = open(self.base_folder / wf_name, 'a', encoding='utf-8', buffering=1)
        self.file_settings = open(self.base_folder / st_name, 'a', encoding='utf-8', buffering=1)

    def log_unidentified_data(self, source_port, data):
        try:
            debug_file = self.base_folder / "startup_debug_log.txt"
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
        self.base_folder.mkdir(parents=True, exist_ok=True)
        db_path = self.base_folder / "syncrone.db"
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
        start_wait = time.monotonic()

        while self.is_running:
            elapsed = time.monotonic() - start_wait
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

                    if self.port_a and self.port_a.in_waiting > 0:
                        data_a = self.port_a.read(self.port_a.in_waiting).decode('latin-1', errors='ignore')
                        self.sig_rx_activity.emit("A")
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

                    if self.port_b and self.port_b.in_waiting > 0:
                        data_b = self.port_b.read(self.port_b.in_waiting).decode('latin-1', errors='ignore')
                        self.sig_rx_activity.emit("B")
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

                except (serial.SerialException, OSError) as e:
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
        self.safe_write_file(self.file_waveform, init_buffer)
        self.db_manager.insert_waveform(self.patient_id, init_buffer)
        self.process_waveform_buffer(init_buffer)

    def handle_waveform(self, data):
        self.safe_write_file(self.file_waveform, data)
        try:
            parsed = self.process_waveform_buffer(data)
            p = parsed[0] if parsed else None
            f = parsed[1] if parsed else None
            self.db_manager.insert_waveform(self.patient_id, data, p, f)
        except:
            self.db_manager.insert_waveform(self.patient_id, data)

    def handle_settings(self, data):
        self.safe_write_file(self.file_settings, data)
        self.db_manager.insert_setting(self.patient_id, data)
        self.process_settings_buffer(data)

    def process_waveform_buffer(self, new_chunk):
        self.waveform_line_buffer += new_chunk
        if len(self.waveform_line_buffer) > self.MAX_BUFFER_SIZE:
            self.waveform_line_buffer = ""
            return None
        last_vals = None
        if '\n' in self.waveform_line_buffer:
            lines = self.waveform_line_buffer.split('\n')
            for line in lines[:-1]:
                clean = line.strip()
                if not clean: continue
                if clean.startswith("BS"):
                    match = self.waveform_pattern.search(clean)
                    if match: self.sig_breath_seq.emit(match.group(1))
                    continue
                if clean.startswith("BE"): continue
                try:
                    parts = clean.split(',')
                    if len(parts) == 2:
                        flow = float(parts[0])
                        pressure = float(parts[1])
                        self.sig_waveform_data.emit(pressure, flow)
                        last_vals = (pressure, flow)
                except ValueError:
                    pass
            self.waveform_line_buffer = lines[-1]
        return last_vals

    def process_settings_buffer(self, new_chunk):
        self.settings_line_buffer += new_chunk
        if len(self.settings_line_buffer) > self.MAX_BUFFER_SIZE:
            self.settings_line_buffer = ""
            return
        if '\r' in self.settings_line_buffer:
            lines = self.settings_line_buffer.split('\r')
            for line in lines[:-1]:
                clean = line.strip()
                if clean:
                    try:
                        parts = clean.split(',')
                        if len(parts) >= 173:
                            mode = parts[7].strip()
                            mandatory = parts[8].strip()
                            spont = parts[9].strip()
                            self.sig_settings_msg.emit(f"Mode: {mandatory} {spont} {mode}")
                    except:
                        pass
            self.settings_line_buffer = lines[-1]

    def log_crash(self, e):
        try:
            with open(self.base_folder / "error_log.txt", "a") as f:
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
        self.render_queue = deque()
        self.render_timer = QTimer()
        self.render_timer.setInterval(20)  # Target 20ms (50Hz)
        self.render_timer.timeout.connect(self.render_loop)

        # Drift & Latency Control Variables
        self.last_render_call = time.monotonic()
        self.fractional_samples = 0.0
        self.last_pkt_time = 0
        self.is_in_silence = False

        # Buffer Synchronization
        self.pending_seq_num = None  # Holds the sequence ID until data arrives
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

        # Trigger Config Alert if needed
        if self.config_corrupt_msg:
            QTimer.singleShot(500, lambda: QMessageBox.warning(self, "Config Reset", self.config_corrupt_msg))

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
        """ Validates structure and converts Units -> Seconds """
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

        # --- WIDGET CREATION START (Must be before adding to layout) ---
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
        self.data_len = 500
        self.x_axis_data = [x * 0.02 for x in range(-self.data_len, 0)]

        self.p_plot = self.plot_widget.addPlot(title="Pressure (cmH2O)")
        self.p_plot.showGrid(x=True, y=True, alpha=0.3)
        self.p_curve = self.p_plot.plot(pen=pg.mkPen('#00ff00', width=2), connect="finite")
        self.p_markers = BreathMarkerManager(self.p_plot)

        self.plot_widget.nextRow()

        self.f_plot = self.plot_widget.addPlot(title="Flow (L/min)")
        self.f_plot.showGrid(x=True, y=True, alpha=0.3)
        self.f_curve = self.f_plot.plot(pen=pg.mkPen('#ffff00', width=2), connect="finite")
        self.f_markers = BreathMarkerManager(self.f_plot)

        self.pressure_data = deque([0] * self.data_len, maxlen=self.data_len)
        self.flow_data = deque([0] * self.data_len, maxlen=self.data_len)

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
        msg = QMessageBox(self)
        msg.setWindowTitle("About")
        msg.setText(f"<b>Syncron-E Waveform Recorder</b><br>v{APP_VERSION}")
        msg.setInformativeText("Autonomous Healthcare, Inc.<br><br>Support: support@autonomoushealthcare.com")
        msg.setIcon(QMessageBox.Information)
        msg.exec()

    def closeEvent(self, event: QCloseEvent):
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
            self.render_queue.clear()
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
            self.worker = VentilatorWorker(self.input_id.text().strip())
            self.worker.sig_status_update.connect(self.update_status)
            self.worker.sig_settings_msg.connect(self.update_mode_display)
            self.worker.sig_breath_seq.connect(self.update_breath_index)
            self.worker.sig_waveform_data.connect(self.update_plot)
            self.worker.sig_rx_activity.connect(self.on_rx_activity)
            self.worker.sig_error.connect(self.handle_worker_error)
            self.worker.sig_connection_lost.connect(self.on_connection_lost)
            self.worker.sig_connection_restored.connect(self.on_connection_restored)
            self.worker.start()

            # 2. Snapshot Worker (New Feature)
            # Pass the path to the DB (it will connect on its own inside the thread)
            db_path = self.worker.base_folder / "syncrone.db"
            self.snapshot_worker = SnapshotWorker(db_path, self.worker.base_folder)
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
        """ Pauses duration timer and updates state """
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
        """ Resumes duration timer """
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
        """
        Stores the breath ID. It will be attached to the NEXT data point
        that arrives via update_plot, ensuring synchronization.
        """
        html = f"<html><head/><body><span style='font-weight:600; color:#ffa500;'>Breath Index:</span> <span style='font-weight:400; color:#ffffff;'>#{seq_num}</span></body></html>"
        self.seq_lbl.setText(html)

        # Store pending sequence to attach to the next data point in the buffer
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
        """
        Received a point from the Serial Worker.
        Attach any pending breath marker, then queue it.
        """
        # Attach seq_num if one is pending, else None
        marker_id = self.pending_seq_num
        self.pending_seq_num = None

        # Queue: (Pressure, Flow, MarkerID)
        self.render_queue.append((p, f, marker_id))

        # Update connection status immediately (visual feedback)
        if self.is_in_silence:
            self.is_in_silence = False
            if not self.is_reconnecting:
                self.update_status("RECORDING", "#00ff00")

    def render_loop(self):
        """
        Continuous Pacer (50Hz) with Pre-Roll Latency Buffer.
        Waits for 500ms of data before starting playback to ensure smoothness.
        If buffer runs dry, it implies true disconnect, so we show NaN gaps.
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
                # Pop (Pressure, Flow, MarkerID)
                p, f, m_id = self.render_queue.popleft()

                self.pressure_data.append(p)
                self.flow_data.append(f)

                # If this point has a Marker ID attached, spawn the marker now
                if m_id:
                    self.p_markers.add_marker(m_id, y_offset=10)
                    self.f_markers.add_marker(m_id, y_offset=10)

                # Synchronize Markers
                self.p_markers.move_all(-0.02)
                self.f_markers.move_all(-0.02)

                did_update = True

                if self.is_in_silence:
                    self.is_in_silence = False
                    self.update_status("RECORDING", "#00ff00")
            else:
                # -- B. STARVATION (Real Disconnect / Empty) --
                # If we run out despite the buffer, it's a real gap.
                self.pressure_data.append(float('nan'))
                self.flow_data.append(float('nan'))

                self.p_markers.move_all(-0.02)
                self.f_markers.move_all(-0.02)
                did_update = True

        # 3. REFRESH GRAPH
        if did_update:
            self.p_curve.setData(self.x_axis_data, list(self.pressure_data))
            self.f_curve.setData(self.x_axis_data, list(self.flow_data))

            # Check for prolonged silence (Alarm)
            if self.render_queue:
                self.last_pkt_time = now

            if (now - self.last_pkt_time) > 5.0:
                if not self.is_in_silence:
                    self.is_in_silence = True
                    self.update_status("SIGNAL LOST", "#ff0000")
                    # Optional: Re-enter buffering state on reconnect
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
            with open(Path.home() / "Desktop" / "Syncron-E Data" / "error_log.txt", "a") as f:
                f.write(f"\n[GUI CRASH {datetime.now()}]\n{error_msg}\n")
        except:
            pass
        sys.__excepthook__(exctype, value, tb)


    sys.excepthook = exception_hook
    app = QApplication(sys.argv)
    window = VentilatorApp()
    window.showMaximized()
    sys.exit(app.exec())