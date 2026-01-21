import sys
import time
import sqlite3
import random
import ctypes
from pathlib import Path
from datetime import datetime

# GUI Components
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QPushButton, QLabel, QFrame, QMessageBox)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer
from PySide6.QtGui import QFont, QColor

# Graphing
import pyqtgraph as pg


# Serial (Mocked for this demo, usually 'import serial')
# from serial.tools import list_ports

# -----------------------------------------------------------------------------
# 1. WORKER THREAD (Handles Serial & Database - Prevents GUI Freezing)
# -----------------------------------------------------------------------------
class VentilatorWorker(QThread):
    # Signals to update GUI
    sig_status_update = Signal(str, str)  # status_msg, color_code
    sig_waveform_data = Signal(float, float)  # pressure, flow
    sig_error = Signal(str)

    def __init__(self, db_path):
        super().__init__()
        self.is_running = False
        self.db_path = db_path

    def run(self):
        """Main acquisition loop."""
        self.is_running = True

        # 1. Setup Database (Safe for Main Thread?) -> Better to open connection here
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            # Enable WAL mode for crash safety
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("""
                           CREATE TABLE IF NOT EXISTS waveforms
                           (
                               timestamp
                               REAL,
                               pressure
                               REAL,
                               flow
                               REAL
                           )
                           """)
            conn.commit()
        except Exception as e:
            self.sig_error.emit(f"Database Error: {e}")
            return

        self.sig_status_update.emit("CONNECTED: LOGGING DATA", "#00ff00")  # Green

        # 2. Fake Data Loop (Replace with your pySerial logic)
        start_time = time.time()
        while self.is_running:
            # --- SIMULATE SERIAL READ ---
            elapsed = time.time() - start_time

            # Simulate "Breathing" waveform
            # Pressure: Goes up and down (0 to 30 cmH2O)
            pressure = 15 + 15 * 0.9 * (elapsed % 4 < 2)  # Square-ish wave
            # Flow: Sine wave
            flow = 40 * 0.8 * (0.5 - (elapsed % 4) / 4)

            # --- SAVE TO DB ---
            # Batch commits in real app for speed, single here for safety
            cursor.execute("INSERT INTO waveforms VALUES (?, ?, ?)",
                           (time.time(), pressure, flow))
            conn.commit()

            # --- SEND TO GUI ---
            self.sig_waveform_data.emit(pressure, flow)

            # Simulate 25Hz sample rate
            time.sleep(0.04)

            # Cleanup
        conn.close()
        self.sig_status_update.emit("STOPPED", "#888888")  # Grey

    def stop(self):
        self.is_running = False
        self.wait()


# -----------------------------------------------------------------------------
# 2. MAIN WINDOW (The Clinical Dashboard)
# -----------------------------------------------------------------------------
class VentilatorApp(QMainWindow):
    def __init__(self):
        super().__init__()

        # --- Window Setup ---
        self.setWindowTitle("Medtronic PB980 Data Logger")
        self.resize(1000, 700)
        self.setStyleSheet("background-color: #1e1e1e; color: #ffffff;")

        # --- Prevent Windows Sleep ---
        self.prevent_sleep()

        # --- Data Storage Path ---
        # Save to User Documents (Non-Admin safe)
        self.save_dir = Path.home() / "Documents" / "VentilatorLogs"
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.save_dir / f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"

        # --- GUI Layout ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        # 1. Header (Status)
        self.header_frame = QFrame()
        self.header_frame.setStyleSheet("background-color: #333; border-radius: 10px;")
        header_layout = QHBoxLayout(self.header_frame)

        self.status_indicator = QLabel("●")
        self.status_indicator.setFont(QFont("Arial", 24))
        self.status_indicator.setStyleSheet("color: #888888;")  # Grey initially

        self.status_label = QLabel("READY TO CONNECT")
        self.status_label.setFont(QFont("Segoe UI", 16, QFont.Bold))

        header_layout.addWidget(self.status_indicator)
        header_layout.addWidget(self.status_label)
        header_layout.addStretch()

        # 2. Graphs (The "Confidence Monitor")
        # Using PyQtGraph for high performance
        pg.setConfigOption('background', '#000000')
        pg.setConfigOption('foreground', '#d0d0d0')

        self.plot_widget = pg.GraphicsLayoutWidget()

        # Pressure Plot (Top)
        self.p_plot = self.plot_widget.addPlot(title="Pressure (cmH2O)")
        self.p_plot.setYRange(0, 40)
        self.p_curve = self.p_plot.plot(pen=pg.mkPen('0f0', width=2))  # Green

        self.plot_widget.nextRow()

        # Flow Plot (Bottom)
        self.f_plot = self.plot_widget.addPlot(title="Flow (L/min)")
        self.f_plot.setYRange(-60, 60)
        self.f_curve = self.f_plot.plot(pen=pg.mkPen('ff0', width=2))  # Yellow

        # Data Buffers for plotting
        self.data_len = 500  # Show last ~20 seconds
        self.pressure_data = [0] * self.data_len
        self.flow_data = [0] * self.data_len

        # 3. Footer (Controls)
        footer_layout = QHBoxLayout()

        self.btn_start = QPushButton("START RECORDING")
        self.btn_start.setMinimumHeight(60)
        self.btn_start.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.btn_start.setStyleSheet("background-color: #007acc; border-radius: 5px;")
        self.btn_start.clicked.connect(self.start_logging)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setMinimumHeight(60)
        self.btn_stop.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.btn_stop.setStyleSheet("background-color: #cc3300; border-radius: 5px;")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_logging)

        footer_layout.addWidget(self.btn_start)
        footer_layout.addWidget(self.btn_stop)

        # Assemble Main Layout
        main_layout.addWidget(self.header_frame, 1)
        main_layout.addWidget(self.plot_widget, 8)
        main_layout.addLayout(footer_layout, 1)

    # --- Logic ---

    def prevent_sleep(self):
        """Tell Windows not to sleep while this app is open."""
        try:
            # ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
        except:
            pass  # Fails gracefully on non-Windows

    def start_logging(self):
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status_label.setText("SEARCHING FOR VENTILATOR...")
        self.status_indicator.setStyleSheet("color: #ffff00;")  # Yellow

        # Start the Worker Thread
        self.worker = VentilatorWorker(str(self.db_path))
        self.worker.sig_status_update.connect(self.update_status)
        self.worker.sig_waveform_data.connect(self.update_plot)
        self.worker.sig_error.connect(self.handle_error)
        self.worker.start()

    def stop_logging(self):
        if hasattr(self, 'worker'):
            self.worker.stop()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    @Slot(str, str)
    def update_status(self, msg, color):
        self.status_label.setText(msg)
        self.status_indicator.setStyleSheet(f"color: {color};")

    @Slot(float, float)
    def update_plot(self, pressure, flow):
        # Roll buffer
        self.pressure_data = self.pressure_data[1:] + [pressure]
        self.flow_data = self.flow_data[1:] + [flow]

        # Update lines
        self.p_curve.setData(self.pressure_data)
        self.f_curve.setData(self.flow_data)

    @Slot(str)
    def handle_error(self, msg):
        self.stop_logging()
        QMessageBox.critical(self, "Connection Error", f"Error: {msg}\n\nPlease check USB cables.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VentilatorApp()
    window.show()
    sys.exit(app.exec())