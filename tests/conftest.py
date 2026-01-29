import sys
import os
from pathlib import Path
from unittest.mock import MagicMock
import pytest

# -------------------------------------------------------------------------
# 1. FIX IMPORT PATH
# -------------------------------------------------------------------------
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

# -------------------------------------------------------------------------
# 2. PRE-MOCK DEPENDENCIES
# -------------------------------------------------------------------------
# We mock these BEFORE 'main' is imported.

# Mock Telemetry
sys.modules['wandb'] = MagicMock()

# Mock Serial
sys.modules['serial'] = MagicMock()
sys.modules['serial.tools'] = MagicMock()
sys.modules['serial.tools.list_ports'] = MagicMock()

# Mock PyQtGraph (CRITICAL for Windows Stability)
# This prevents OpenGL drivers from loading
mock_pg = MagicMock()
sys.modules['pyqtgraph'] = mock_pg

# NOW it is safe to import main
import main


# -------------------------------------------------------------------------
# 3. DEFINE FIXTURES
# -------------------------------------------------------------------------
@pytest.fixture
def mock_serial_ports(mocker):
    mock_port_a = MagicMock(device="COM3", vid=0x0403, pid=0x6001)
    mock_port_b = MagicMock(device="COM4", vid=0x0403, pid=0x6001)

    mocker.patch('serial.tools.list_ports.comports', return_value=[mock_port_a, mock_port_b])

    mock_inst = MagicMock(in_waiting=0, is_open=True)
    mocker.patch('serial.Serial', return_value=mock_inst)

    return [mock_port_a, mock_port_b]


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_syncrone.db"
    manager = main.DatabaseManager(str(db_file))
    manager.connect()
    yield manager
    manager.close()