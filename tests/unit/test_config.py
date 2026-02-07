import pytest
import json
from unittest.mock import MagicMock, mock_open
# We import main to make sure it's loaded, but we patch components on it
from syncrone import main
from syncrone.main import VentilatorApp


class TestConfigSystem:
    """
    Tests for configuration loading, parsing, and error handling.
    """

    @pytest.fixture
    def app(self, qapp, mocker):
        """
        Creates a headless VentilatorApp instance for testing config methods.
        'qapp' argument is REQUIRED to initialize the Qt Event Loop.
        """
        # 1. Mock UI and System calls
        mocker.patch('main.VentilatorApp.init_ui')
        mocker.patch('main.VentilatorApp.prevent_sleep')
        mocker.patch('main.VentilatorApp.check_disk_space', return_value=10 ** 12)

        # 2. Mock Workers (Threads)
        mocker.patch('main.VentilatorWorker')
        mocker.patch('main.SnapshotWorker')

        # 3. Mock Telemetry (The likely cause of the crash)
        # This replaces the class in main.py, so self.telemetry becomes a MagicMock
        mocker.patch('main.TelemetryManager')

        # 4. Mock QTimer (The user-identified suspect)
        # This prevents the render timer from ever connecting to the C++ event loop
        mocker.patch('main.QTimer')

        # Now it is safe to create the window because all complex objects are fake
        app = VentilatorApp()
        return app

    def test_unit_conversion_logic(self, app):
        """
        Verify that _process_options correctly converts human time units to seconds.
        """
        raw_options = [
            {"label": "1 Min", "type": "time", "value": 1, "unit": "minutes"},
            {"label": "1 Hour", "type": "time", "value": 1, "unit": "hours"},
            {"label": "1 Day", "type": "time", "value": 1, "unit": "days"},
            {"label": "1 Week", "type": "time", "value": 1, "unit": "weeks"},
            {"label": "5000 Breaths", "type": "breaths", "value": 5000, "unit": "breaths"}
        ]

        processed = app._process_options(raw_options)

        assert processed[0]["value"] == 60
        assert processed[1]["value"] == 3600
        assert processed[2]["value"] == 86400
        assert processed[3]["value"] == 604800
        assert processed[4]["value"] == 5000

    def test_load_valid_config_file(self, app, mocker):
        """
        Mock a valid .config.json file and ensure it loads.
        """
        valid_json = json.dumps({
            "options": [
                {"label": "Custom 10s", "type": "time", "value": 10, "unit": "seconds"}
            ]
        })

        mocker.patch("builtins.open", mock_open(read_data=valid_json))
        mocker.patch("pathlib.Path.exists", return_value=True)

        result = app.load_config()

        assert len(result) == 1
        assert result[0]["label"] == "Custom 10s"
        assert result[0]["value"] == 10

    def test_corrupt_config_fallback(self, app, mocker):
        """
        If .config.json is malformed, backup and load defaults.
        """
        mocker.patch("builtins.open", mock_open(read_data="{ INVALID JSON_"))
        mocker.patch("pathlib.Path.exists", return_value=True)
        mock_move = mocker.patch("shutil.move")

        result = app.load_config()

        # Defaults list in main.py has 9 items
        assert len(result) == 9
        assert result[0]["label"] == "Manual Stop (Unlimited)"

        mock_move.assert_called_once()
        args, _ = mock_move.call_args
        assert ".config_CORRUPT_" in str(args[1])

    def test_missing_config_creates_defaults(self, app, mocker):
        """
        If .config.json does not exist, it should be created with defaults.
        """
        mocker.patch("pathlib.Path.exists", return_value=False)
        mock_file = mock_open()
        mocker.patch("builtins.open", mock_file)

        result = app.load_config()

        assert len(result) == 9

        # Verify file was written
        mock_file.assert_called_with(app.base_folder / ".config.json", "w")
        handle = mock_file()
        assert handle.write.called

    def test_unknown_unit_raises_error(self, app):
        """
        Manual sabotage test: Unknown units should error out.
        """
        bad_options = [
            {"label": "Bad Time", "type": "time", "value": 1, "unit": "fortnights"}
        ]

        with pytest.raises(ValueError, match="Unknown unit: fortnights"):
            app._process_options(bad_options)