import sys
import time
import math
import ctypes
from pathlib import Path
from datetime import datetime

# Serial Communication
import serial
import serial.tools.list_ports

# GUI Components
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QPushButton, QLabel, QFrame, QMessageBox)
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QFont

# Graphing
import pyqtgraph as pg


# -----------------------------------------------------------------------------
# 1. WORKER THREAD (Handles Serial Comm & Data Generation)
# -----------------------------------------------------------------------------
class VentilatorWorker(QThread):
    # Signals
    sig_status_update = Signal(str, str)  # status_msg, color_code
    sig_waveform_data = Signal(float, float)  # pressure (top), flow (bottom)
    sig_error = Signal(str)

    def __init__(self):
        super().__init__()
        self.is_running = False
        self.serial_port = None

        # Target Device Identity (FTDI)
        self.TARGET_VID = 0x0403
        self.TARGET_PID = 0x6001

    def find_target_device(self):
        """Scans ports for the specific FTDI cable."""
        ports = serial.tools.list_ports.comports()
        for port in ports:
            if port.vid == self.TARGET_VID and port.pid == self.TARGET_PID:
                return port.device
        return None

    def run(self):
        self.is_running = True

        # --- 1. Auto-Detect Port ---
        self.sig_status_update.emit("SCANNING FOR CABLE...", "#ffff00")  # Yellow
        target_com_port = self.find_target_device()

        if not target_com_port:
            self.sig_error.emit("Syncron-E Cable (FTDI) not found!\nPlease check USB connection.")
            return

        # --- 2. Connect ---
        try:
            self.serial_port = serial.Serial(
                port=target_com_port,
                baudrate=9600,
                timeout=1
            )
            self.sig_status_update.emit(f"CONNECTED: {target_com_port}", "#00ff00")  # Green
        except serial.SerialException as e:
            self.sig_error.emit(f"Could not open {target_com_port}.\nIs it in use?\nError: {e}")
            return

        # --- 3. Main Loop (Simulate Data & Write Serial) ---
        start_time = time.time()
        last_serial_write = 0

        try:
            while self.is_running:
                current_time = time.time()
                elapsed = current_time - start_time

                # A. Generate Dummy Waveforms
                # Top: Cosine (Simulating Pressure 0-30)
                pressure = 15 + 15 * math.cos(elapsed * 2)

                # Bottom: Sine (Simulating Flow -60 to 60)
                flow = 60 * math.sin(elapsed * 2)

                # Send to GUI (High frequency: ~25Hz)
                self.sig_waveform_data.emit(pressure, flow)

                # B. Write to Serial Port (Every 1.0 second)
                if current_time - last_serial_write >= 1.0:
                    msg = f"Hello World - {datetime.now().strftime('%H:%M:%S')}\n"
                    self.serial_port.write(msg.encode('utf-8'))
                    last_serial_write = current_time

                # Control loop speed (approx 25Hz refresh rate)
                time.sleep(0.04)

        except Exception as e:
            self.sig_error.emit(f"Runtime Error: {e}")
        finally:
            if self.serial_port and self.serial_port.is_open:
                self.serial_port.close()
            self.sig_status_update.emit("STOPPED", "#888888")

    def stop(self):
        self.is_running = False
        self.wait()


# -----------------------------------------------------------------------------
# 2. MAIN WINDOW
# -----------------------------------------------------------------------------
class VentilatorApp(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Syncron-E Clinical Data Logger")
        self.resize(1000, 700)
        self.setStyleSheet("background-color: #1e1e1e; color: #ffffff;")
        self.prevent_sleep()

        # --- Layout Setup ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # 1. Header
        self.header_frame = QFrame()
        self.header_frame.setStyleSheet("background-color: #333; border-radius: 8px;")
        header_layout = QHBoxLayout(self.header_frame)

        self.status_indicator = QLabel("●")
        self.status_indicator.setFont(QFont("Arial", 28))
        self.status_indicator.setStyleSheet("color: #888888;")

        self.status_label = QLabel("READY TO CONNECT")
        self.status_label.setFont(QFont("Segoe UI", 16, QFont.Bold))

        header_layout.addWidget(self.status_indicator)
        header_layout.addWidget(self.status_label)
        header_layout.addStretch()

        # 2. Waveform Graphs (PyQtGraph)
        pg.setConfigOption('background', '#000000')
        pg.setConfigOption('foreground', '#d0d0d0')
        pg.setConfigOptions(antialias=True)

        self.plot_widget = pg.GraphicsLayoutWidget()

        # -- Top Plot: Pressure (Cosine) --
        self.p_plot = self.plot_widget.addPlot(title="Pressure (cmH2O)")
        self.p_plot.setYRange(0, 40)
        self.p_plot.showGrid(x=True, y=True, alpha=0.3)
        self.p_curve = self.p_plot.plot(pen=pg.mkPen('#00ff00', width=2))  # Green

        self.plot_widget.nextRow()

        # -- Bottom Plot: Flow (Sine) --
        self.f_plot = self.plot_widget.addPlot(title="Flow (L/min)")
        self.f_plot.setYRange(-70, 70)
        self.f_plot.showGrid(x=True, y=True, alpha=0.3)
        self.f_curve = self.f_plot.plot(pen=pg.mkPen('#ffff00', width=2))  # Yellow

        # Data Buffers (Scrolling window)
        self.data_len = 200  # Number of points to display
        self.pressure_data = [0] * self.data_len
        self.flow_data = [0] * self.data_len

        # 3. Footer Controls
        footer_layout = QHBoxLayout()
        self.btn_start = QPushButton("START LOGGING")
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

        # Add to main layout
        main_layout.addWidget(self.header_frame, 1)
        main_layout.addWidget(self.plot_widget, 8)
        main_layout.addLayout(footer_layout, 1)

    def prevent_sleep(self):
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
        except:
            pass

    def start_logging(self):
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

        self.worker = VentilatorWorker()
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
        # Update buffers (Scroll left)
        self.pressure_data = self.pressure_data[1:] + [pressure]
        self.flow_data = self.flow_data[1:] + [flow]

        # Redraw lines
        self.p_curve.setData(self.pressure_data)
        self.f_curve.setData(self.flow_data)

    @Slot(str)
    def handle_error(self, msg):
        self.stop_logging()
        QMessageBox.critical(self, "Error", msg)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VentilatorApp()
    window.show()
    sys.exit(app.exec())