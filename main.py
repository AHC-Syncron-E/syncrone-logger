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
                               QHBoxLayout, QPushButton, QLabel, QFrame, QMessageBox,
                               QLineEdit, QSpacerItem, QSizePolicy)
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QFont, QIcon, QColor

# Graphing
import pyqtgraph as pg


# -----------------------------------------------------------------------------
# 1. WORKER THREAD (Handles Serial Comm & Data Generation)
# -----------------------------------------------------------------------------
class VentilatorWorker(QThread):
    # Signals
    sig_status_update = Signal(str, str)  # status_msg, color_code
    sig_mode_update = Signal(str)  # New: Updates the "Mode: ..." text
    sig_waveform_data = Signal(float, float)  # pressure (top), flow (bottom)
    sig_error = Signal(str)

    def __init__(self, patient_id, db_path):
        super().__init__()
        self.patient_id = patient_id
        self.db_path = db_path
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

            # --- 3. Mock Settings Parsing ---
            # Simulate receiving a settings packet immediately after connection
            time.sleep(0.5)
            # In the real app, this string comes from parsing the serial response
            parsed_mode = "Mode: VC A/C"
            self.sig_mode_update.emit(parsed_mode)

        except serial.SerialException as e:
            self.sig_error.emit(f"Could not open {target_com_port}.\nIs it in use?\nError: {e}")
            return

        # --- 4. Main Loop (Simulate Waveforms & Write Serial) ---
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
                    # Include Patient ID in the serial log for tracking
                    msg = f"ID:{self.patient_id} - {datetime.now().strftime('%H:%M:%S')}\n"
                    self.serial_port.write(msg.encode('utf-8'))
                    last_serial_write = current_time

                # Control loop speed
                time.sleep(0.04)

        except Exception as e:
            self.sig_error.emit(f"Runtime Error: {e}")
        finally:
            if self.serial_port and self.serial_port.is_open:
                self.serial_port.close()
            self.sig_status_update.emit("STOPPED", "#888888")
            self.sig_mode_update.emit("Mode: --")  # Reset mode on stop

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
        self.resize(1000, 750)
        self.setStyleSheet("background-color: #1e1e1e; color: #ffffff;")

        # Load Icon (Ensure icon.ico is in the same folder)
        if Path("icon.ico").exists():
            self.setWindowIcon(QIcon("icon.ico"))

        self.prevent_sleep()
        self.save_dir = Path.home() / "Documents" / "VentilatorLogs"
        self.save_dir.mkdir(parents=True, exist_ok=True)

        # --- Layout Setup ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # 1. Header (Status + Mode Display)
        self.header_frame = QFrame()
        self.header_frame.setStyleSheet("background-color: #333; border-radius: 8px;")
        header_layout = QHBoxLayout(self.header_frame)

        # Status Circle & Text
        self.status_indicator = QLabel("●")
        self.status_indicator.setFont(QFont("Arial", 28))
        self.status_indicator.setStyleSheet("color: #888888;")

        self.status_label = QLabel("READY")
        self.status_label.setFont(QFont("Segoe UI", 16, QFont.Bold))

        # Spacer to push Mode to the right
        spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        # Mode Display (The "Settings" Indicator)
        self.mode_label = QLabel("Mode: --")
        self.mode_label.setFont(QFont("Segoe UI", 16, QFont.Bold))
        self.mode_label.setStyleSheet("color: #00aaff;")  # Light blue to distinguish from status

        header_layout.addWidget(self.status_indicator)
        header_layout.addWidget(self.status_label)
        header_layout.addItem(spacer)
        header_layout.addWidget(self.mode_label)
        header_layout.addSpacing(20)

        # 2. Waveform Graphs
        pg.setConfigOption('background', '#000000')
        pg.setConfigOption('foreground', '#d0d0d0')
        pg.setConfigOptions(antialias=True)

        self.plot_widget = pg.GraphicsLayoutWidget()

        # Pressure Plot
        self.p_plot = self.plot_widget.addPlot(title="Pressure (cmH2O)")
        self.p_plot.setYRange(0, 40)
        self.p_plot.showGrid(x=True, y=True, alpha=0.3)
        self.p_curve = self.p_plot.plot(pen=pg.mkPen('#00ff00', width=2))

        self.plot_widget.nextRow()

        # Flow Plot
        self.f_plot = self.plot_widget.addPlot(title="Flow (L/min)")
        self.f_plot.setYRange(-70, 70)
        self.f_plot.showGrid(x=True, y=True, alpha=0.3)
        self.f_curve = self.f_plot.plot(pen=pg.mkPen('#ffff00', width=2))

        # Data Buffers
        self.data_len = 200
        self.pressure_data = [0] * self.data_len
        self.flow_data = [0] * self.data_len

        # 3. Footer (Patient ID + Controls)
        footer_layout = QVBoxLayout()

        # Patient ID Input Row
        id_layout = QHBoxLayout()
        lbl_id = QLabel("Patient ID / Session Identifier:")
        lbl_id.setFont(QFont("Segoe UI", 12))
        lbl_id.setStyleSheet("color: #cccccc;")

        self.input_patient_id = QLineEdit()
        self.input_patient_id.setPlaceholderText("Enter Identifier (e.g. PT-101)...")
        self.input_patient_id.setFont(QFont("Segoe UI", 12))
        self.input_patient_id.setStyleSheet("""
            QLineEdit { 
                padding: 5px; 
                border-radius: 4px; 
                border: 1px solid #555;
                background-color: #2b2b2b;
                color: white;
            }
            QLineEdit:focus { border: 1px solid #007acc; }
        """)

        id_layout.addWidget(lbl_id)
        id_layout.addWidget(self.input_patient_id)

        # Buttons Row
        btn_layout = QHBoxLayout()
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

        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)

        footer_layout.addLayout(id_layout)
        footer_layout.addSpacing(10)
        footer_layout.addLayout(btn_layout)

        # Assemble Main Layout
        main_layout.addWidget(self.header_frame, 1)
        main_layout.addWidget(self.plot_widget, 8)
        main_layout.addLayout(footer_layout, 1)

    def prevent_sleep(self):
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
        except:
            pass

    def start_logging(self):
        # 1. Validate Input
        patient_id = self.input_patient_id.text().strip()
        if not patient_id:
            QMessageBox.warning(self, "Input Required", "Please enter a Patient or Session Identifier.")
            self.input_patient_id.setFocus()
            return

        # 2. Prepare Session
        self.input_patient_id.setEnabled(False)  # Lock input while running
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

        # Generate Filename with ID
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        db_filename = self.save_dir / f"syncrone_{patient_id}_{timestamp}.db"

        # 3. Start Worker
        self.worker = VentilatorWorker(patient_id, str(db_filename))
        self.worker.sig_status_update.connect(self.update_status)
        self.worker.sig_mode_update.connect(self.update_mode)  # Connect new signal
        self.worker.sig_waveform_data.connect(self.update_plot)
        self.worker.sig_error.connect(self.handle_error)
        self.worker.start()

    def stop_logging(self):
        if hasattr(self, 'worker'):
            self.worker.stop()

        self.input_patient_id.setEnabled(True)  # Unlock input
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.mode_label.setText("Mode: --")

    @Slot(str, str)
    def update_status(self, msg, color):
        self.status_label.setText(msg)
        self.status_indicator.setStyleSheet(f"color: {color};")

    @Slot(str)
    def update_mode(self, mode_text):
        self.mode_label.setText(mode_text)

    @Slot(float, float)
    def update_plot(self, pressure, flow):
        self.pressure_data = self.pressure_data[1:] + [pressure]
        self.flow_data = self.flow_data[1:] + [flow]
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