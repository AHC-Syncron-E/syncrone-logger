import sys
import time
import psutil
import wandb
import os
from datetime import datetime

from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout,
                               QLabel, QWidget, QPlainTextEdit, QPushButton,
                               QComboBox, QHBoxLayout, QLineEdit, QFrame)
from PySide6.QtCore import QThread, Signal, QTimer, Qt
from PySide6.QtGui import QFont, QIcon, QPalette, QColor

# --- CONFIGURATION ---
WANDB_PROJECT = "REDACTED_PROJECT"
WANDB_API_KEY = os.environ.get("WANDB_API_KEY")


class MonitorWorker(QThread):
    log_msg = Signal(str)
    stats_update = Signal(dict)
    finished = Signal()

    def __init__(self, target_pid):
        super().__init__()
        self.target_pid = target_pid
        self.is_running = True
        self.process = None

    def run(self):
        try:
            self.process = psutil.Process(self.target_pid)
            name = self.process.name()
            self.log_msg.emit(f"ATTACHED: {name} (PID: {self.target_pid})")

            self.init_wandb()

            while self.is_running:
                if not self.process.is_running():
                    self.log_msg.emit("Target process terminated.")
                    break

                with self.process.oneshot():
                    mem = self.process.memory_info()
                    cpu = self.process.cpu_percent(interval=None)
                    threads = self.process.num_threads()
                    handles = self.process.num_handles() if os.name == 'nt' else 0

                metrics = {
                    "app_rss_mb": mem.rss / (1024 * 1024),
                    "app_vms_mb": mem.vms / (1024 * 1024),
                    "app_cpu_percent": cpu,
                    "app_threads": threads,
                    "app_handles": handles,
                    "system_cpu_percent": psutil.cpu_percent(),
                    "system_ram_percent": psutil.virtual_memory().percent
                }

                wandb.log(metrics)
                self.stats_update.emit(metrics)

                # Sample Rate: ~5 seconds
                for _ in range(50):
                    if not self.is_running: break
                    time.sleep(0.1)

        except Exception as e:
            self.log_msg.emit(f"ERROR: {e}")
        finally:
            try:
                wandb.finish()
            except:
                pass
            self.finished.emit()

    def init_wandb(self):
        try:
            if not WANDB_API_KEY:
                raise ValueError("WANDB_API_KEY not found in environment variables.")

            wandb.login(key=WANDB_API_KEY)
            wandb.init(project=WANDB_PROJECT,
                       name=f"watchdog_{datetime.now().strftime('%m%d_%H%M')}",
                       settings=wandb.Settings(start_method="thread"))
            self.log_msg.emit("WandB Telemetry Connected.")
        except Exception as e:
            self.log_msg.emit(f"WandB Connection Failed: {e}")

    def stop(self):
        self.is_running = False
        self.wait()


class WatchdogApp(QMainWindow):
    def __init__(self):
        super().__init__()

        # --- SETUP ---
        self.setWindowTitle("Syncron-E Telemetry")
        self.resize(700, 550)

        # Icon Setup (Looks for icon.ico in the script's directory)
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
            icon_path = os.path.join(base_dir, "assets", "icon.ico")
        else:
            # Go up 3 levels from src/syncrone/watchdog.py
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
            icon_path = os.path.join(base_dir, "assets", "icon.ico")

        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # --- THEME (Claude-inspired Warm/Cream) ---
        # Palette:
        # Background: #FDFBF7 (Cream)
        # Card Bg: #FFFFFF (White)
        # Accent: #D97757 (Terracotta/Warm Orange)
        # Text: #383838 (Soft Black)
        # Borders: #E6E2D6

        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #FDFBF7;
                color: #383838;
                font-family: "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QLabel {
                color: #555555;
            }
            /* Inputs */
            QLineEdit, QComboBox {
                background-color: #FFFFFF;
                border: 1px solid #E6E2D6;
                border-radius: 6px;
                padding: 6px;
                color: #333;
                selection-background-color: #F0EBE0;
            }
            QComboBox::drop-down {
                border: 0px;
            }
            /* Standard Buttons */
            QPushButton {
                background-color: #F2EFE9;
                border: 1px solid #DCD8CF;
                border-radius: 6px;
                padding: 6px 12px;
                color: #4A4A4A;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #EAE6DE;
                border-color: #D1CCC0;
            }
            /* Primary Action Button (Terracotta) */
            QPushButton#PrimaryBtn {
                background-color: #DA7756;
                color: #FFFFFF;
                border: 1px solid #C86645;
            }
            QPushButton#PrimaryBtn:hover {
                background-color: #C66342;
            }
            /* Stop Button (Red-ish) */
            QPushButton#StopBtn {
                background-color: #D95757;
                color: #FFFFFF;
                border: 1px solid #C04040;
            }
            /* Stats Cards */
            QLabel#StatBox {
                background-color: #FFFFFF;
                border: 1px solid #EAE6DE;
                border-radius: 8px;
                padding: 15px;
                font-weight: bold;
                font-size: 15px;
                color: #222;
            }
            /* Log View */
            QPlainTextEdit {
                background-color: #FFFFFF;
                border: 1px solid #E6E2D6;
                border-radius: 8px;
                padding: 5px;
                font-family: "Consolas", "Monaco", monospace;
                font-size: 13px;
                color: #555;
            }
        """)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(25, 25, 25, 25)

        # --- HEADER / FILTER ---
        # Container for selection tools
        filter_frame = QFrame()
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(0, 0, 0, 0)

        # 1. Filter Input
        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("Filter by Name or PID... (e.g. 'python')")
        self.txt_filter.setFixedWidth(200)
        self.txt_filter.returnPressed.connect(self.populate_processes)

        # 2. Process Dropdown
        self.combo_procs = QComboBox()
        self.combo_procs.setPlaceholderText("Select process...")

        # 3. Refresh Button
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.populate_processes)

        # 4. Attach Button
        self.btn_attach = QPushButton("Start Recording")
        self.btn_attach.setObjectName("PrimaryBtn")  # Connects to CSS ID
        self.btn_attach.setCursor(Qt.PointingHandCursor)
        self.btn_attach.clicked.connect(self.start_monitoring)

        filter_layout.addWidget(self.txt_filter)
        filter_layout.addWidget(self.combo_procs, 1)
        filter_layout.addWidget(self.btn_refresh)
        filter_layout.addWidget(self.btn_attach)

        main_layout.addWidget(filter_frame)

        # --- STATUS INDICATOR ---
        self.lbl_status = QLabel("Ready to Monitor")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.lbl_status.setStyleSheet("color: #999; letter-spacing: 1px; text-transform: uppercase;")
        main_layout.addWidget(self.lbl_status)

        # --- METRICS GRID ---
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(15)

        self.lbl_ram = self.create_stat_box("RAM (RSS)")
        self.lbl_cpu = self.create_stat_box("CPU Usage")
        self.lbl_handles = self.create_stat_box("Handles")

        stats_layout.addWidget(self.lbl_ram)
        stats_layout.addWidget(self.lbl_cpu)
        stats_layout.addWidget(self.lbl_handles)

        main_layout.addLayout(stats_layout)

        # --- LOGGING AREA ---
        log_lbl = QLabel("Activity Log")
        log_lbl.setStyleSheet("font-weight: bold; font-size: 13px; margin-top: 10px;")
        main_layout.addWidget(log_lbl)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        main_layout.addWidget(self.log_view)

        # --- INIT WORKER ---
        self.worker = None

        # Initial populate
        QTimer.singleShot(100, self.populate_processes)

    def create_stat_box(self, title):
        lbl = QLabel(f"{title}\n--")
        lbl.setObjectName("StatBox")
        lbl.setAlignment(Qt.AlignCenter)
        return lbl

    def populate_processes(self):
        """Populate dropdown, respecting the filter text."""
        current_selection = self.combo_procs.currentData()
        self.combo_procs.clear()

        filter_text = self.txt_filter.text().lower().strip()
        candidates = []

        # Loop processes
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                pid = proc.info['pid']
                name = proc.info['name']
                cmd = proc.info['cmdline'] or []
                cmd_str = " ".join(cmd)

                # Create search string
                search_corpus = f"{name} {pid} {cmd_str}".lower()

                # --- FILTER LOGIC ---
                # If filter is empty, show everything (maybe limit to first 500 to save UI lag?)
                # If filter exists, strictly match.
                if filter_text and filter_text not in search_corpus:
                    continue

                # Display Text formatting
                display_text = f"{name} ({pid})"
                if "python" in name.lower() and len(cmd) > 1:
                    script_name = os.path.basename(cmd[1])
                    display_text += f" [{script_name}]"

                self.combo_procs.addItem(display_text, pid)

                # Track auto-select candidates
                if "main.py" in cmd_str or "syncron" in name.lower():
                    candidates.append(display_text)

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if self.combo_procs.count() == 0:
            self.combo_procs.addItem("No matching processes found", -1)
        else:
            # Try to restore previous selection
            idx = self.combo_procs.findData(current_selection)
            if idx >= 0:
                self.combo_procs.setCurrentIndex(idx)
            # Or select first candidate
            elif candidates:
                idx = self.combo_procs.findText(candidates[0])
                if idx >= 0: self.combo_procs.setCurrentIndex(idx)

        self.log_msg(f"Process list refreshed. Found {self.combo_procs.count()} matches.")

    def start_monitoring(self):
        if self.worker is not None:
            # STOPPING
            self.worker.stop()
            self.worker = None
            self.btn_attach.setText("Start Recording")
            self.btn_attach.setObjectName("PrimaryBtn")
            self.btn_attach.setStyle(self.btn_attach.style())  # Force refresh style
            self.lbl_status.setText("Ready to Monitor")
            self.lbl_status.setStyleSheet("color: #999;")
            self.combo_procs.setEnabled(True)
            self.txt_filter.setEnabled(True)
            return

        # STARTING
        idx = self.combo_procs.currentIndex()
        pid = self.combo_procs.itemData(idx)

        if pid is None or pid == -1:
            self.log_msg("Invalid process selection.")
            return

        target_name = self.combo_procs.currentText()

        self.worker = MonitorWorker(pid)
        self.worker.log_msg.connect(self.log_msg)
        self.worker.stats_update.connect(self.update_stats)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

        self.btn_attach.setText("Stop Monitoring")
        self.btn_attach.setObjectName("StopBtn")
        self.btn_attach.setStyle(self.btn_attach.style())  # Force refresh style

        self.lbl_status.setText(f"Monitoring: {target_name}")
        self.lbl_status.setStyleSheet("color: #DA7756; font-weight: bold;")  # Warm accent color

        self.combo_procs.setEnabled(False)
        self.txt_filter.setEnabled(False)

    def update_stats(self, data):
        # Update cards with nicer formatting
        self.lbl_ram.setText(f"RAM (RSS)\n{data['app_rss_mb']:.1f} MB")
        self.lbl_cpu.setText(f"CPU Usage\n{data['app_cpu_percent']:.1f}%")
        self.lbl_handles.setText(f"Handles\n{data['app_handles']}")

    def on_worker_finished(self):
        self.combo_procs.setEnabled(True)
        self.txt_filter.setEnabled(True)
        self.btn_attach.setText("Start Recording")
        self.btn_attach.setObjectName("PrimaryBtn")
        self.btn_attach.setStyle(self.btn_attach.style())

        self.lbl_status.setText("Target Lost / Stopped")
        self.lbl_status.setStyleSheet("color: #D95757;")  # Red-ish
        self.worker = None

    def log_msg(self, text):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{ts}] {text}")


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Optional: Increase global font size for the "polished" feel
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = WatchdogApp()
    window.show()
    sys.exit(app.exec())