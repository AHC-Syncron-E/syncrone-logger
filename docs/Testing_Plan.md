# Syncron-E Waveform Recorder -- Test Plan

**Version:** 3.0
**Status:** CI Pipeline Active
**Last Updated:** 2026-04-06

---

## 1. Test Coverage Summary

| Component | Status | Test File | Method |
| :--- | :--- | :--- | :--- |
| Waveform Parser | Complete | `tests/unit/test_pure_logic.py` | Unit test against `parse_incoming_chunk()` static method |
| Settings Parser | Complete | `tests/unit/test_pure_logic.py` | Unit test against `parse_settings_chunk()` static method |
| Config Loader | Complete | `tests/unit/test_config.py` | Mocked filesystem, corruption fallback |
| EDF Snapshot | Complete | `tests/unit/test_snapshot.py` | Real SQLite + edfio, atomic write |
| Database Persistence | Complete | `tests/integration/test_database.py` | Real SQLite file, schema, WAL mode |
| Reconnection Logic | Complete | `tests/integration/test_reconnection.py` | Mock serial, self-healing from SerialException |
| UI Smoke Test | Complete | `tests/e2e/test_ui.py` | Headless Qt (offscreen), widget state |
| Regression Baseline | Complete | `tests/test_regression_baseline.py` | Golden-master parsing, buffer overflow |

---

## 2. Architecture for Testability

The parsing logic is implemented as **static pure-function methods** on `VentilatorWorker`, decoupled from the Qt thread loop and serial I/O:

- `VentilatorWorker.parse_incoming_chunk(buffer, data)` -- returns `(remaining_buffer, events)` list
- `VentilatorWorker.parse_settings_chunk(buffer, data)` -- returns `(remaining_buffer, messages)` list

This allows exhaustive unit testing without hardware, serial mocks, or GUI instantiation.

---

## 3. Test Fixtures (`tests/conftest.py`)

Pre-mocks applied **before** `import main`:

- `serial`, `serial.tools`, `serial.tools.list_ports` -- prevents hardware access
- `pyqtgraph` -- prevents OpenGL driver loading (critical for headless CI)

Fixtures:

- `mock_serial_ports` -- two mock COM ports with FTDI VID/PID
- `temp_db` -- fresh `DatabaseManager` instance per test (real SQLite file in `tmp_path`)

---

## 4. Running Tests

```bash
# Full suite with coverage
QT_QPA_PLATFORM=offscreen pytest tests/ --cov=main --cov-report=term

# Single test file
pytest tests/unit/test_pure_logic.py -v

# Generate HTML reports (matches CI)
pytest tests/ --cov=main --cov-report=html:coverage_html_report --html=test_results_report.html --self-contained-html
```

---

## 5. CI Pipeline

Tests run automatically on push and PR to `main` via `.github/workflows/test_suite.yml`:

- **Platform:** `windows-latest` (matches deployment target)
- **Python:** 3.13
- **Artifacts:** Test results HTML report and coverage HTML report uploaded on every run
