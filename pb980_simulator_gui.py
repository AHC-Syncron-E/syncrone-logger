import os
import sys
import time
from datetime import datetime
from pathlib import Path

import serial
import serial.tools.list_ports
from PySide6.QtCore import QThread, Signal, Slot

# GUI Components
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# --- CONFIGURATION ---
BAUD_RATE_WAVE = 38400
BAUD_RATE_SETT = 9600
LOG_FILE = "logged_waveforms.txt"
SAMPLE_RATE_MS = 0.02
SUPPORTED_VIDS = [0x067B, 0x0403]  # Prolific, FTDI

# --- PAYLOAD DATA ---
# Synthetic PB980 MISCF payload for simulator testing.
# Format matches PB980 serial protocol (see PB980 Owner's Manual).
SIMULATED_PB980_PAYLOAD = (
    b'MISCF,1225,169 ,\x0212:00 ,980 SIM000000001  ,JAN 01 2026 ,INVASIVE ,A/C   ,VC    ,'
    b'      ,V-Trig,10.0  ,0.400 ,44.0  ,21    ,      ,0.0   ,0.0   ,60    ,      ,10.0  ,'
    b'      ,100   ,15    ,0.74  ,      ,PC    ,1.00  ,7.11  ,      ,RAMP  ,OFF   ,100   ,'
    b'      ,48.500,0.100 ,1370  ,210   ,1370  ,210   ,OFF   ,      ,3.5   ,2.0   ,      ,'
    b'      ,      ,      ,      ,         ,      ,      ,HME                ,      ,Enabled  ,40    ,'
    b'      ,      ,      ,50.0  ,      ,      ,      ,      ,      ,ADULT     ,      ,      ,14.0  ,'
    b'10.0  ,0.320 ,3.200 ,14.0  ,2.4   ,5.00  ,1:5.00,22    ,      ,      ,      ,      ,      ,'
    b'      ,      ,      ,      ,0.3   ,      ,      ,0.0   ,0.0   ,0.0   ,0.0   ,0.0   ,      ,'
    b'26.0  ,9.9   ,      ,39.0  ,0.0   ,OFF   ,0.0   ,0.0   ,0.000 ,OFF   ,NORMAL,NORMAL,NORMAL,'
    b'NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,'
    b'NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,'
    b'NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,'
    b'NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,NORMAL,      ,      ,ON    ,'
    b'19    ,0.3   ,57    ,NORMAL,      ,      ,      ,      ,      ,      ,      ,      ,      ,'
    b'      ,      ,      ,      ,\x03\r'
)


# --- HELPER LOGIC ---

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for Nuitka/MSIX """
    try:
        # Check if we are running as a compiled Nuitka/PyInstaller exe
        if getattr(sys, 'frozen', False):
            # In Nuitka standalone, resources are next to the executable
            base_path = os.path.dirname(sys.executable)
        else:
            # In dev mode, resources are next to the script
            base_path = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def get_breaths(filepath):
    p = Path(filepath)
    if not p.exists():
        # Raise generic error to be caught by UI loader
        raise FileNotFoundError(f"Path does not exist: {p}")

    breaths = []
    current_lines = []
    data_line_count = 0
    in_breath = False

    with open(p, encoding='utf-8') as f:
        for line in f:
            clean = line.strip()
            if not clean:
                continue

            if clean.startswith("BS"):
                in_breath = True
                current_lines = [line]
                data_line_count = 0
            elif clean.startswith("BE"):
                if in_breath:
                    current_lines.append(line)
                    duration = data_line_count * SAMPLE_RATE_MS
                    breaths.append({
                        'payload': "".join(current_lines).encode('latin-1'),
                        'duration': duration,
                        'header': current_lines[0].strip()
                    })
                    in_breath = False
                    current_lines = []
            elif in_breath:
                current_lines.append(line)
                data_line_count += 1

    return breaths


# --- WORKER THREADS ---

class WaveformWorker(QThread):
    sig_log = Signal(str, str)  # (Message, Color)
    sig_error = Signal(str)

    def __init__(self, port, breaths):
        super().__init__()
        self.port_name = port
        self.breaths = breaths
        self.is_running = True
        self.ser = None

    def run(self):
        try:
            self.ser = serial.Serial(self.port_name, BAUD_RATE_WAVE, timeout=0)
            self.sig_log.emit(f"Waveform Port Opened: {self.port_name}", "#00ff00")
        except Exception as e:
            self.sig_error.emit(f"Failed to open Waveform Port: {e}")
            return

        index = 0
        total_breaths = len(self.breaths)
        next_wake_time = time.monotonic()

        try:
            while self.is_running:
                breath = self.breaths[index]

                # Write Data
                if self.ser and self.ser.is_open:
                    self.ser.write(breath['payload'])
                    self.ser.flush()

                # Calculate Drift
                now = time.monotonic()
                current_lag = now - next_wake_time

                # Log EVERY breath (Sequence checking)
                msg = f"Sent {breath['header']} | Lag: {current_lag * 1000:.1f}ms"
                self.sig_log.emit(msg, "#aaaaaa")

                # Schedule
                next_wake_time += breath['duration']
                sleep_duration = next_wake_time - time.monotonic()

                if sleep_duration > 0:
                    time.sleep(sleep_duration)
                elif sleep_duration < -1.0:
                    self.sig_log.emit("Warning: Simulator drift >1s. Resetting clock.", "#ffaa00")
                    next_wake_time = time.monotonic()

                index = (index + 1) % total_breaths

        except Exception as e:
            self.sig_error.emit(f"Waveform Loop Error: {e}")
        finally:
            if self.ser and self.ser.is_open:
                self.ser.close()

    def stop(self):
        self.is_running = False
        self.wait()


class SettingsWorker(QThread):
    sig_log = Signal(str, str)
    sig_error = Signal(str)

    def __init__(self, port):
        super().__init__()
        self.port_name = port
        self.is_running = True
        self.ser = None

    def run(self):
        try:
            self.ser = serial.Serial(self.port_name, BAUD_RATE_SETT, timeout=0.1)
            # Flush stale data immediately
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            self.sig_log.emit(f"Settings Port Listening: {self.port_name}", "#00aaff")
        except Exception as e:
            self.sig_error.emit(f"Failed to open Settings Port: {e}")
            return

        buffer = b""
        last_response_time = 0.0
        MIN_RESPONSE_INTERVAL = 1.0

        while self.is_running:
            try:
                if self.ser.in_waiting > 0:
                    chunk = self.ser.read(self.ser.in_waiting)
                    buffer += chunk

                    if b'\r' in buffer:
                        parts = buffer.split(b'\r')
                        buffer = parts[-1]  # Keep tail
                        commands = parts[:-1]

                        for cmd in commands:
                            decoded = cmd.decode('ascii', errors='ignore').strip()
                            if decoded == "SNDF":
                                now = time.monotonic()
                                if (now - last_response_time) > MIN_RESPONSE_INTERVAL:
                                    self.ser.write(SIMULATED_PB980_PAYLOAD)
                                    self.ser.flush()
                                    self.sig_log.emit("Received SNDF -> Sent Settings Payload", "#00ff00")
                                    last_response_time = now
                                else:
                                    self.sig_log.emit("Ignored rapid-fire SNDF (Debounce)", "#ffaa00")
                else:
                    time.sleep(0.05)
            except Exception as e:
                self.sig_error.emit(f"Settings Loop Error: {e}")
                break

        if self.ser and self.ser.is_open:
            self.ser.close()

    def stop(self):
        self.is_running = False
        self.wait()


# --- MAIN GUI ---
class SimulatorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PB980 Ventilator Simulator")
        self.resize(600, 500)
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #1e1e1e; color: #ffffff; font-family: 'Segoe UI', sans-serif; }
            QGroupBox { border: 1px solid #444; border-radius: 6px; margin-top: 20px; font-weight: bold; color: #ccc; }
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 5px; }
            QLabel { color: #aaa; font-size: 13px; }
            QComboBox { background-color: #333; color: white; border: 1px solid #555; padding: 5px; border-radius: 4px; }
            QComboBox:disabled { background-color: #222; color: #555; border: 1px solid #333; }
            QPushButton { background-color: #007acc; color: white; border: none; padding: 8px 15px; border-radius: 4px; font-weight: bold; font-size: 14px; }
            QPushButton:hover { background-color: #0099ff; }
            QPushButton:disabled { background-color: #333; color: #666; }
            QPlainTextEdit { background-color: #111; color: #ddd; border: 1px solid #444; font-family: Consolas, monospace; font-size: 12px; }
        """)

        self.is_simulating = False
        self.breaths_data = []
        self.wave_worker = None
        self.sett_worker = None

        self.init_ui()
        self.load_data()
        self.refresh_ports()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # 1. Header
        header_lbl = QLabel("PB980 Hardware Simulator")
        header_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff; margin-bottom: 10px;")
        layout.addWidget(header_lbl)

        # 2. Configuration Panel
        config_grp = QGroupBox("Connection Settings")
        config_layout = QGridLayout()
        config_layout.setSpacing(10)

        # Waveform Port
        config_layout.addWidget(QLabel("Waveform Port (Output):"), 0, 0)
        self.combo_wave = QComboBox()
        config_layout.addWidget(self.combo_wave, 0, 1)

        # Settings Port
        config_layout.addWidget(QLabel("Settings Port (Input):"), 1, 0)
        self.combo_sett = QComboBox()
        config_layout.addWidget(self.combo_sett, 1, 1)

        # Refresh Button
        self.btn_refresh = QPushButton("⟳ Refresh Ports")
        self.btn_refresh.setFixedWidth(120)
        self.btn_refresh.setStyleSheet("background-color: #444; font-size: 12px;")
        self.btn_refresh.clicked.connect(self.refresh_ports)
        config_layout.addWidget(self.btn_refresh, 0, 2, 2, 1)

        config_grp.setLayout(config_layout)
        layout.addWidget(config_grp)

        # 3. Status & Data Info
        self.lbl_status = QLabel("Status: Idle")
        self.lbl_status.setStyleSheet("font-weight: bold; color: #888; margin-top: 5px;")
        self.lbl_breaths = QLabel("Breaths Loaded: 0")

        info_layout = QHBoxLayout()
        info_layout.addWidget(self.lbl_status)
        info_layout.addStretch()
        info_layout.addWidget(self.lbl_breaths)
        layout.addLayout(info_layout)

        # 4. Action Button
        self.btn_toggle = QPushButton("START SIMULATION")
        self.btn_toggle.setMinimumHeight(50)
        self.btn_toggle.clicked.connect(self.toggle_simulation)
        layout.addWidget(self.btn_toggle)

        # 5. Log Output
        layout.addWidget(QLabel("Simulation Log:"))
        self.log_display = QPlainTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setMaximumBlockCount(1000)  # Keep memory low
        layout.addWidget(self.log_display)

    def load_data(self):
        # Locate the bundled file, whether in dev mode or MSIX mode
        file_path = resource_path(LOG_FILE)

        try:
            self.breaths_data = get_breaths(file_path)
            self.lbl_breaths.setText(f"Breaths Loaded: {len(self.breaths_data)}")
        except Exception as e:
            self.log_msg(f"Error: {e}", "#ff0000")
            self.log_msg(f"Searched at: {file_path}", "#ff0000")
            self.btn_toggle.setEnabled(False)

    def refresh_ports(self):
        self.combo_wave.clear()
        self.combo_sett.clear()

        ports = serial.tools.list_ports.comports()
        valid_ports = []

        for p in ports:
            # Check VID if available
            is_supported = (p.vid in SUPPORTED_VIDS) if p.vid else False
            name = f"{p.device}"
            if not is_supported and p.vid:
                name += f" (VID: {hex(p.vid)})"
            elif p.description:
                name += f" - {p.description}"

            item_data = p.device

            # Add to lists
            self.combo_wave.addItem(name, item_data)
            self.combo_sett.addItem(name, item_data)

            if is_supported:
                valid_ports.append(item_data)

        # Intelligent Default Selection
        if len(valid_ports) >= 2:
            # Sort to ensure consistent ordering (e.g. USB0, USB1)
            valid_ports.sort()

            index_wave = self.combo_wave.findData(valid_ports[0])
            self.combo_wave.setCurrentIndex(index_wave)

            index_sett = self.combo_sett.findData(valid_ports[1])
            self.combo_sett.setCurrentIndex(index_sett)

            self.log_msg("Auto-selected supported ports.", "#00ff00")
        else:
            self.log_msg(f"Found {len(valid_ports)} supported cables. Please select manually.", "#ffa500")

    def toggle_simulation(self):
        if not self.is_simulating:
            self.start_simulation()
        else:
            self.stop_simulation()

    def start_simulation(self):
        w_port = self.combo_wave.currentData()
        s_port = self.combo_sett.currentData()

        if not w_port or not s_port:
            QMessageBox.warning(self, "Port Error", "Please select valid ports for both connections.")
            return

        if w_port == s_port:
            QMessageBox.warning(self, "Port Conflict", "Waveform and Settings cannot use the same port.")
            return

        # UI Updates
        self.is_simulating = True
        self.btn_toggle.setText("STOP SIMULATION")
        self.btn_toggle.setStyleSheet("background-color: #cc3300; color: white; font-weight: bold; font-size: 16px;")
        self.combo_wave.setEnabled(False)
        self.combo_sett.setEnabled(False)
        self.btn_refresh.setEnabled(False)
        self.lbl_status.setText("Status: RUNNING")
        self.lbl_status.setStyleSheet("font-weight: bold; color: #00ff00;")

        # Start Workers
        self.wave_worker = WaveformWorker(w_port, self.breaths_data)
        self.wave_worker.sig_log.connect(self.log_msg)
        self.wave_worker.sig_error.connect(self.handle_error)
        self.wave_worker.start()

        self.sett_worker = SettingsWorker(s_port)
        self.sett_worker.sig_log.connect(self.log_msg)
        self.sett_worker.sig_error.connect(self.handle_error)
        self.sett_worker.start()

    def stop_simulation(self):
        self.is_simulating = False

        # Stop Workers
        if self.wave_worker:
            self.wave_worker.stop()
            self.wave_worker = None

        if self.sett_worker:
            self.sett_worker.stop()
            self.sett_worker = None

        # UI Updates
        self.btn_toggle.setText("START SIMULATION")
        self.btn_toggle.setStyleSheet("background-color: #007acc; color: white; font-weight: bold; font-size: 14px;")
        self.combo_wave.setEnabled(True)
        self.combo_sett.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.lbl_status.setText("Status: STOPPED")
        self.lbl_status.setStyleSheet("font-weight: bold; color: #888;")
        self.log_msg("Simulation Stopped.", "#ffffff")

    @Slot(str)
    def handle_error(self, msg):
        self.log_msg(f"ERROR: {msg}", "#ff0000")
        self.stop_simulation()
        QMessageBox.critical(self, "Simulation Error", msg)

    @Slot(str, str)
    def log_msg(self, msg, color="#cccccc"):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        html = f'<span style="color: #666;">[{ts}]</span> <span style="color: {color};">{msg}</span>'
        self.log_display.appendHtml(html)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SimulatorApp()
    window.show()
    sys.exit(app.exec())
