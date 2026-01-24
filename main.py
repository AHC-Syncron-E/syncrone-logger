import sys
import time
import math
import ctypes
import re
import shutil
import sqlite3
import traceback
import os
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque

# Serial Communication
import serial
import serial.tools.list_ports

# GUI Components
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QPushButton, QLabel, QFrame, QMessageBox,
                               QLineEdit, QSpacerItem, QSizePolicy)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer
from PySide6.QtGui import QFont, QIcon, QColor

# Graphing
import pyqtgraph as pg


# -----------------------------------------------------------------------------
# 1. DATABASE MANAGER (Robust Schema Handling)
# -----------------------------------------------------------------------------
class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.conn = None
        self.cursor = None

    def connect(self):
        # 1. Schema Check / Migration Strategy
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

            if "waveforms" not in self._get_tables(self.db_path):
                return False

            if "parsed_pressure" not in columns:
                return True
            return False
        except Exception:
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
            print(f"[DB] Schema mismatch. Backup created: {backup_name.name}")
        except Exception as e:
            print(f"[DB] Backup failed: {e}")

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
        self.conn.commit()

    def insert_waveform(self, session_id, raw_data, pressure=None, flow=None):
        ts = datetime.now().isoformat()
        self.conn.execute(
            "INSERT INTO waveforms (session_id, timestamp, raw_data, parsed_pressure, parsed_flow) VALUES (?, ?, ?, ?, ?)",
            (session_id, ts, raw_data, pressure, flow)
        )

    def insert_setting(self, session_id, raw_data):
        ts = datetime.now().isoformat()
        self.conn.execute(
            "INSERT INTO settings (session_id, timestamp, raw_data) VALUES (?, ?, ?)",
            (session_id, ts, raw_data)
        )
        self.conn.commit()

    def commit_batch(self):
        if self.conn:
            self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()


# -----------------------------------------------------------------------------
# 2. WORKER THREAD
# -----------------------------------------------------------------------------
class VentilatorWorker(QThread):
    # Signals
    sig_status_update = Signal(str, str)
    sig_settings_msg = Signal(str)
    sig_waveform_data = Signal(float, float)
    sig_breath_seq = Signal(str)
    sig_error = Signal(str)

    # New Signal: Activity Indicator (Payload: "A" or "B")
    sig_rx_activity = Signal(str)

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

        self.SUPPORTED_DEVICES = [
            (0x0403, 0x6001),
            (0x067B, 0x23A3),
            (0x067B, 0x2303),
        ]

        self.waveform_pattern = re.compile(r"BS,\s*S:(\d+),")

    # --- File Management ---
    def open_log_files(self):
        self.base_folder.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_file_date = datetime.now().date()

        wf_name = f"waveforms_{timestamp}.txt"
        st_name = f"settings_{timestamp}.txt"

        self.file_waveform = open(self.base_folder / wf_name, 'w', encoding='utf-8', buffering=1)
        self.file_settings = open(self.base_folder / st_name, 'w', encoding='utf-8', buffering=1)

    def log_unidentified_data(self, source_port, data):
        """Logs raw data during the identification phase to help debug format issues."""
        try:
            debug_file = self.base_folder / "startup_debug_log.txt"
            with open(debug_file, "a", encoding='utf-8') as f:
                # Escaping newlines so one packet equals one line in the debug file
                clean_data = data.replace('\n', '\\n').replace('\r', '\\r')
                timestamp = datetime.now().strftime("%H:%M:%S.%f")
                f.write(f"[{timestamp}] [{source_port}] {clean_data}\n")
        except Exception as e:
            print(f"Debug write failed: {e}")

    def check_file_rotation(self):
        now = time.monotonic()
        if now - self.last_rotation_check < 60:
            return

        self.last_rotation_check = now
        if datetime.now().date() > self.current_file_date:
            self.sig_status_update.emit("ROTATING FILES...", "#00aaff")
            if self.file_waveform: self.file_waveform.close()
            if self.file_settings: self.file_settings.close()
            self.open_log_files()
            self.sig_status_update.emit("LOGGING (Rotated)", "#00ff00")

    def safe_write_file(self, file_handle, data):
        if file_handle:
            try:
                file_handle.write(data)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            except Exception as e:
                print(f"Write failed: {e}")

    # --- Setup & Cleanup ---
    def setup_system(self):
        self.base_folder.mkdir(parents=True, exist_ok=True)
        db_path = self.base_folder / "syncrone.db"
        self.db_manager = DatabaseManager(str(db_path))
        self.db_manager.connect()
        self.open_log_files()

    def close_system(self):
        if self.port_a and self.port_a.is_open: self.port_a.close()
        if self.port_b and self.port_b.is_open: self.port_b.close()
        if self.db_manager: self.db_manager.close()
        if self.file_waveform: self.file_waveform.close()
        if self.file_settings: self.file_settings.close()

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

    # --- Main Loop ---
    def run(self):
        self.is_running = True

        try:
            self.sig_status_update.emit("SCANNING PORTS...", "#ffff00")
            found_devices = self.get_valid_ports()

            if len(found_devices) < 2:
                self.sig_error.emit(f"Found {len(found_devices)} cable(s).\nNeed exactly 2.")
                self.sig_status_update.emit("CONNECTION FAILED", "#ff0000")
                return

            dev_a, dev_b = found_devices[0], found_devices[1]

            self.setup_system()

            self.port_a = serial.Serial(dev_a, timeout=0)
            self.configure_port(self.port_a, 38400)

            self.port_b = serial.Serial(dev_b, timeout=0)
            self.configure_port(self.port_b, 38400)

            self.sig_status_update.emit("IDENTIFYING PORTS...", "#00aaff")

            start_time = time.monotonic()
            last_serial_write = start_time
            last_db_commit = start_time

            loop_interval = 0.004
            next_wake_time = time.monotonic() + loop_interval

            ports_identified = False

            while self.is_running:
                now = time.monotonic()
                self.check_file_rotation()

                # Read Port A
                if self.port_a.in_waiting > 0:
                    data_a = self.port_a.read(self.port_a.in_waiting).decode('latin-1', errors='ignore')

                    # 1. Fire Activity Signal (Visual Feedback)
                    self.sig_rx_activity.emit("A")

                    if not ports_identified:
                        # 2. Log Debug Data (Observability)
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

                # Read Port B
                if self.port_b.in_waiting > 0:
                    data_b = self.port_b.read(self.port_b.in_waiting).decode('latin-1', errors='ignore')

                    # 1. Fire Activity Signal (Visual Feedback)
                    self.sig_rx_activity.emit("B")

                    if not ports_identified:
                        # 2. Log Debug Data (Observability)
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

                # Database Commit
                if now - last_db_commit >= 1.0:
                    self.db_manager.commit_batch()
                    last_db_commit = now

                # Settings Polling
                if ports_identified and (now - last_serial_write >= 5.0):
                    msg = "SNDF\r"
                    try:
                        self.settings_port.write(msg.encode('ascii'))
                        self.settings_port.flush()
                        last_serial_write = now
                    except Exception as e:
                        pass

                # Metronome
                sleep_duration = next_wake_time - time.monotonic()
                if sleep_duration > 0:
                    time.sleep(sleep_duration)
                else:
                    next_wake_time = time.monotonic()
                next_wake_time += loop_interval

        except Exception as e:
            self.log_crash(e)
            self.sig_error.emit(f"Runtime Error: {e}")
        finally:
            self.close_system()
            self.sig_status_update.emit("STOPPED", "#888888")

    def assign_ports(self, wave_port, set_port, init_buffer, name):
        self.waveform_port = wave_port
        self.settings_port = set_port
        self.configure_port(self.settings_port, 9600)

        w_name = self.waveform_port.port
        s_name = self.settings_port.port
        status_msg = f"LOGGING | Waveforms: {w_name} | Settings: {s_name}"
        self.sig_status_update.emit(status_msg, "#00ff00")

        self.safe_write_file(self.file_waveform, init_buffer)
        self.db_manager.insert_waveform(self.patient_id, init_buffer)
        self.process_waveform_buffer(init_buffer)

    def handle_waveform(self, data):
        self.safe_write_file(self.file_waveform, data)
        try:
            parsed_vals = self.process_waveform_buffer(data)
            p_val = parsed_vals[0] if parsed_vals else None
            f_val = parsed_vals[1] if parsed_vals else None
            self.db_manager.insert_waveform(self.patient_id, data, p_val, f_val)
        except Exception:
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
                if not clean:
                    continue

                # Check for Breath Start (BS) to parse Sequence Number
                if clean.startswith("BS"):
                    match = self.waveform_pattern.search(clean)
                    if match:
                        seq_num = match.group(1)
                        self.sig_breath_seq.emit(seq_num)
                    continue

                if clean.startswith("BE"):
                    continue

                # Parse "Flow, Pressure"
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
                            mandatory_type = parts[8].strip()
                            spont_type = parts[9].strip()

                            raw_str = f"Mode: {mandatory_type} {spont_type} {mode}"
                            display_str = " ".join(raw_str.split())
                            self.sig_settings_msg.emit(display_str)
                    except Exception:
                        pass

            self.settings_line_buffer = lines[-1]

    def log_crash(self, e):
        try:
            log_path = self.base_folder / "error_log.txt"
            with open(log_path, "a") as f:
                f.write(f"\n[CRASH {datetime.now()}] {str(e)}\n{traceback.format_exc()}\n")
        except:
            pass

    def stop(self):
        self.is_running = False
        self.wait()


# -----------------------------------------------------------------------------
# 3. MAIN WINDOW
# -----------------------------------------------------------------------------
class VentilatorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Syncron-E Clinical Data Logger")
        self.resize(1200, 800)
        self.setStyleSheet("background-color: #1e1e1e; color: #ffffff;")
        self.is_logging = False

        if Path("icon.ico").exists():
            self.setWindowIcon(QIcon("icon.ico"))
        self.prevent_sleep()

        # UI Setup
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Header
        header = QFrame()
        header.setStyleSheet("background-color: #333; border-radius: 8px;")
        h_layout = QHBoxLayout(header)

        self.status_dot = QLabel("●")
        self.status_dot.setFont(QFont("Arial", 28))
        self.status_dot.setStyleSheet("color: #888;")

        self.status_lbl = QLabel("READY")
        self.status_lbl.setFont(QFont("Segoe UI", 14, QFont.Bold))

        self.seq_lbl = QLabel("Breath Index: --")
        self.seq_lbl.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.seq_lbl.setStyleSheet("color: #ffa500; margin-left: 20px;")

        # --- NEW: RX LEDs Setup ---
        rx_font = QFont("Segoe UI", 10, QFont.Bold)

        # RX A Label & LED
        lbl_rx_a = QLabel("RX A:")
        lbl_rx_a.setFont(rx_font)
        self.led_a = QLabel()
        self.led_a.setFixedSize(16, 16)
        self.led_a.setStyleSheet("background-color: #111; border-radius: 8px; border: 1px solid #555;")
        self.led_a_timer = QTimer()
        self.led_a_timer.setSingleShot(True)
        self.led_a_timer.timeout.connect(
            lambda: self.led_a.setStyleSheet("background-color: #111; border-radius: 8px; border: 1px solid #555;"))

        # RX B Label & LED
        lbl_rx_b = QLabel("RX B:")
        lbl_rx_b.setFont(rx_font)
        self.led_b = QLabel()
        self.led_b.setFixedSize(16, 16)
        self.led_b.setStyleSheet("background-color: #111; border-radius: 8px; border: 1px solid #555;")
        self.led_b_timer = QTimer()
        self.led_b_timer.setSingleShot(True)
        self.led_b_timer.timeout.connect(
            lambda: self.led_b.setStyleSheet("background-color: #111; border-radius: 8px; border: 1px solid #555;"))
        # ---------------------------

        self.mode_lbl = QLabel("Mode: --")
        self.mode_lbl.setFont(QFont("Segoe UI", 16, QFont.Bold))
        self.mode_lbl.setStyleSheet("color: #00aaff;")

        h_layout.addWidget(self.status_dot)
        h_layout.addWidget(self.status_lbl)
        h_layout.addWidget(self.seq_lbl)

        # Add RX Indicators to Header
        h_layout.addSpacing(40)
        h_layout.addWidget(lbl_rx_a)
        h_layout.addWidget(self.led_a)
        h_layout.addSpacing(15)
        h_layout.addWidget(lbl_rx_b)
        h_layout.addWidget(self.led_b)

        h_layout.addStretch()
        h_layout.addWidget(self.mode_lbl)
        h_layout.addSpacing(20)

        # Graphs
        pg.setConfigOption('background', '#000000')
        pg.setConfigOption('foreground', '#d0d0d0')
        pg.setConfigOptions(antialias=True)
        self.plot_widget = pg.GraphicsLayoutWidget()

        # X-Axis Time Array
        self.data_len = 500
        self.x_axis_data = [x * 0.02 for x in range(-self.data_len, 0)]

        # Pressure Plot
        self.p_plot = self.plot_widget.addPlot(title="Pressure (cmH2O)")
        self.p_plot.enableAutoRange(axis='y')
        self.p_plot.showGrid(x=True, y=True, alpha=0.3)
        self.p_plot.setLabel('bottom', "Time", units='s')
        self.p_curve = self.p_plot.plot(pen=pg.mkPen('#00ff00', width=2))

        self.plot_widget.nextRow()

        # Flow Plot
        self.f_plot = self.plot_widget.addPlot(title="Flow (L/min)")
        self.f_plot.enableAutoRange(axis='y')
        self.f_plot.showGrid(x=True, y=True, alpha=0.3)
        self.f_plot.setLabel('bottom', "Time", units='s')
        self.f_curve = self.f_plot.plot(pen=pg.mkPen('#ffff00', width=2))

        # Data Deques
        self.pressure_data = deque([0] * self.data_len, maxlen=self.data_len)
        self.flow_data = deque([0] * self.data_len, maxlen=self.data_len)

        # Footer
        footer = QVBoxLayout()
        id_layout = QHBoxLayout()

        self.input_id = QLineEdit()
        self.input_id.setPlaceholderText("Enter Patient ID...")
        self.input_id.setStyleSheet("padding: 5px; background: #2b2b2b; color: white; border: 1px solid #555;")
        self.input_id.textChanged.connect(self.check_input)

        self.btn_action = QPushButton("START LOGGING")
        self.btn_action.setMinimumHeight(60)
        self.btn_action.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.btn_action.setStyleSheet("background-color: #444; color: #888; border-radius: 5px;")
        self.btn_action.setEnabled(False)
        self.btn_action.clicked.connect(self.toggle_logging)

        id_layout.addWidget(QLabel("Patient ID:"))
        id_layout.addWidget(self.input_id)
        footer.addLayout(id_layout)
        footer.addWidget(self.btn_action)

        layout.addWidget(header, 1)
        layout.addWidget(self.plot_widget, 8)
        layout.addLayout(footer, 1)

    def prevent_sleep(self):
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
        except:
            pass

    def check_input(self):
        if self.is_logging: return
        if self.input_id.text().strip():
            self.btn_action.setEnabled(True)
            self.btn_action.setStyleSheet("background-color: #007acc; color: white; border-radius: 5px;")
        else:
            self.btn_action.setEnabled(False)
            self.btn_action.setStyleSheet("background-color: #444; color: #888; border-radius: 5px;")

    def toggle_logging(self):
        if not self.is_logging:
            self.worker = VentilatorWorker(self.input_id.text().strip())
            self.worker.sig_status_update.connect(self.update_status)
            self.worker.sig_settings_msg.connect(self.mode_lbl.setText)
            self.worker.sig_breath_seq.connect(self.update_breath_index)
            self.worker.sig_waveform_data.connect(self.update_plot)
            self.worker.sig_rx_activity.connect(self.on_rx_activity)  # Connect new LED signal
            self.worker.sig_error.connect(lambda m: (self.worker.stop(), QMessageBox.critical(self, "Error", m)))
            self.worker.start()

            self.is_logging = True
            self.input_id.setEnabled(False)
            self.btn_action.setText("STOP")
            self.btn_action.setStyleSheet("background-color: #cc3300; color: white; border-radius: 5px;")
        else:
            if hasattr(self, 'worker'): self.worker.stop()
            self.is_logging = False
            self.input_id.setEnabled(True)
            self.btn_action.setText("START LOGGING")
            self.check_input()

    @Slot(str, str)
    def update_status(self, msg, color):
        self.status_lbl.setText(msg)
        self.status_dot.setStyleSheet(f"color: {color};")

    @Slot(str)
    def update_breath_index(self, seq_num):
        self.seq_lbl.setText(f"Breath Index: {seq_num}")

    @Slot(str)
    def on_rx_activity(self, port_id):
        """Flashes the corresponding LED green for 50ms on data receipt."""
        style_on = "background-color: #00ff00; border-radius: 8px; border: 1px solid #555;"

        if port_id == "A":
            self.led_a.setStyleSheet(style_on)
            self.led_a_timer.start(50)
        elif port_id == "B":
            self.led_b.setStyleSheet(style_on)
            self.led_b_timer.start(50)

    @Slot(float, float)
    def update_plot(self, p, f):
        self.pressure_data.append(p)
        self.flow_data.append(f)
        self.p_curve.setData(self.x_axis_data, list(self.pressure_data))
        self.f_curve.setData(self.x_axis_data, list(self.flow_data))


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
    window.show()
    sys.exit(app.exec())