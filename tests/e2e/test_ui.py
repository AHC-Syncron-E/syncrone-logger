import pytest
from PySide6.QtWidgets import QWidget, QPushButton, QLineEdit, QLabel, QComboBox
from PySide6.QtCore import Qt
from unittest.mock import MagicMock

# Import the application
import main


class TestUserInterface:
    """
    Logic-Focused UI Tests.
    BYPASSES APP INITIALIZATION ENTIRELY to prevent Windows Access Violations.
    We test the methods (check_input, toggle_logging) on a manually configured instance.
    """

    @pytest.fixture
    def ui_app(self, qapp, qtbot, mocker):
        # 1. Create a BLANK instance of the App
        # CRITICAL: We use __new__ to skip __init__ entirely.
        # This guarantees NO crashing code (timers, icons, styles) ever runs.
        app = main.VentilatorApp.__new__(main.VentilatorApp)

        # 2. Manually Initialize Standard QMainWindow basics
        # We must call the parent QMainWindow init to make it a valid Qt widget
        super(main.VentilatorApp, app).__init__()

        # 3. Manually Initialize App State (The variables normally set in __init__)
        app.is_logging = False
        app.is_locked = False
        app.has_data_started = False
        app.is_reconnecting = False
        app.accumulated_duration = 0.0
        app.session_breath_count = 0
        app.base_folder = MagicMock()

        # 4. Mock the Backend Components usually created in __init__
        app.worker = MagicMock()
        app.snapshot_worker = MagicMock()
        app.telemetry = MagicMock()
        app.render_timer = MagicMock()
        app.ui_timer = MagicMock()

        # 5. Inject UI Widgets (Fresh, Clean QWidgets)
        app.input_id = QLineEdit()
        app.btn_action = QPushButton("START RECORDING")
        app.btn_lock = QPushButton("LOCK APP")
        app.combo_stop = QComboBox()
        app.combo_stop.addItem("Test Option", {"type": "manual", "value": 0})

        # Mock other labels to prevent AttributeErrors in logic
        app.lbl_started = QLabel()
        app.lbl_duration = QLabel()
        app.lbl_breaths = QLabel()
        app.lbl_disk = QLabel()
        app.status_lbl = QLabel()
        app.status_dot = QLabel()
        app.led_a = QLabel()
        app.led_b = QLabel()
        app.mode_lbl = QLabel()
        app.seq_lbl = QLabel()

        # Mock Plots (Heavy objects)
        app.p_plot = MagicMock()
        app.f_plot = MagicMock()
        app.p_plot.getViewBox.return_value = MagicMock()
        app.f_plot.getViewBox.return_value = MagicMock()

        # 6. Manually Connect Signals (Replicating init_ui logic)
        app.input_id.textChanged.connect(app.check_input)
        app.btn_action.clicked.connect(app.toggle_logging)
        app.btn_lock.clicked.connect(app.toggle_lock)

        # 7. Mock System Calls that methods might use
        mocker.patch('main.VentilatorApp.check_disk_space', return_value=1_000_000_000_000)
        # Mock worker constructors
        mocker.patch('main.VentilatorWorker', return_value=MagicMock())
        mocker.patch('main.SnapshotWorker', return_value=MagicMock())

        # 8. Register with qtbot
        qtbot.addWidget(app)

        return app

    def test_smoke_launch(self, ui_app):
        """Verify our manual setup matches expected initial state."""
        assert ui_app.is_logging is False
        assert ui_app.is_locked is False
        assert ui_app.btn_action.isEnabled() is True

        # Run check_input once to set correct button state
        ui_app.check_input()
        assert ui_app.btn_action.isEnabled() is False

    def test_input_validation_workflow(self, ui_app, qtbot):
        """Verify that typing a Patient ID enables the Start button."""
        ui_app.check_input()
        assert ui_app.btn_action.isEnabled() is False

        qtbot.keyClicks(ui_app.input_id, "PATIENT_XYZ")

        assert ui_app.btn_action.isEnabled() is True
        assert ui_app.input_id.text() == "PATIENT_XYZ"

        ui_app.input_id.clear()
        assert ui_app.btn_action.isEnabled() is False

    def test_start_recording_workflow(self, ui_app, qtbot):
        """Verify clicking 'Start' locks UI and starts worker."""
        ui_app.input_id.setText("TEST_CASE_01")

        qtbot.mouseClick(ui_app.btn_action, Qt.LeftButton)

        assert ui_app.is_logging is True
        assert ui_app.input_id.isEnabled() is False
        assert "STOP" in ui_app.btn_action.text()

        ui_app.worker.start.assert_called_once()

    def test_stop_recording_workflow(self, ui_app, qtbot):
        """Verify clicking 'Stop' resets the UI."""
        ui_app.input_id.setText("TEST_CASE_01")
        qtbot.mouseClick(ui_app.btn_action, Qt.LeftButton)

        qtbot.mouseClick(ui_app.btn_action, Qt.LeftButton)

        assert ui_app.is_logging is False
        assert ui_app.input_id.isEnabled() is True
        assert "START" in ui_app.btn_action.text()

        ui_app.worker.stop.assert_called_once()

    def test_lock_screen_logic(self, ui_app, qtbot):
        """Verify Lock/Unlock button logic."""
        qtbot.mouseClick(ui_app.btn_lock, Qt.LeftButton)
        assert ui_app.is_locked is True
        assert ui_app.input_id.isEnabled() is False

        qtbot.mouseClick(ui_app.btn_lock, Qt.LeftButton)
        assert ui_app.is_locked is False
        assert ui_app.input_id.isEnabled() is True