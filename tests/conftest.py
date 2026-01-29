import sys
import os
from pathlib import Path
from unittest.mock import MagicMock
import pytest

# -------------------------------------------------------------------------
# 1. FIX IMPORT PATH
# -------------------------------------------------------------------------
# Get the absolute path to the project root (one level up from 'tests')
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

# -------------------------------------------------------------------------
# 2. PRE-MOCK HARDWARE & OS DEPENDENCIES
# -------------------------------------------------------------------------
# We must mock these BEFORE 'main' is imported to prevent crashes or
# side effects (like trying to open a real COM port or calling Windows DLLs).

# Mock Windows Ctypes (Prevents crashes on non-Windows or just safeguards)
mock_ctypes = MagicMock()
sys.modules['ctypes'] = mock_ctypes
sys.modules['ctypes.windll'] = MagicMock()
sys.modules['ctypes.windll.kernel32'] = MagicMock()

# Mock Telemetry (WandB) - Prevent network calls
mock_wandb = MagicMock()
sys.modules['wandb'] = mock_wandb

# Mock Serial Ports - We don't have physical cables connected
mock_serial = MagicMock()
sys.modules['serial'] = mock_serial
sys.modules['serial.tools'] = MagicMock()
sys.modules['serial.tools.list_ports'] = MagicMock()

# NOW it is safe to import main
import main


# -------------------------------------------------------------------------
# 3. DEFINE FIXTURES
# -------------------------------------------------------------------------

@pytest.fixture
def mock_serial_ports(mocker):
    """
    Simulates finding 2 valid FTDI cables so the worker starts up successfully.
    """
    mock_port_a = MagicMock()
    mock_port_a.device = "COM3"
    mock_port_a.vid = 0x0403
    mock_port_a.pid = 0x6001

    mock_port_b = MagicMock()
    mock_port_b.device = "COM4"
    mock_port_b.vid = 0x0403
    mock_port_b.pid = 0x6001

    # Patch the list_ports function to return our fake ports
    mocker.patch('serial.tools.list_ports.comports', return_value=[mock_port_a, mock_port_b])

    # Also patch serial.Serial class to return a mock object when instantiated
    mock_serial_instance = MagicMock()
    mock_serial_instance.in_waiting = 0
    mock_serial_instance.is_open = True
    mocker.patch('serial.Serial', return_value=mock_serial_instance)

    return [mock_port_a, mock_port_b]


@pytest.fixture
def temp_db(tmp_path):
    """
    Creates a temporary SQLite database that is deleted after the test.
    """
    db_file = tmp_path / "test_syncrone.db"
    manager = main.DatabaseManager(str(db_file))
    manager.connect()
    yield manager
    manager.close()