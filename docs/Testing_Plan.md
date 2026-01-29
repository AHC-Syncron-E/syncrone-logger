# Syncron-E Waveform Recorder - Automated Test Plan (v2)

**Version:** 2.1
**Status:** Core Pipeline Complete / UI Testing Pending
**Last Updated:** Phase 4 Complete

---

## 1. Accomplished Coverage

| Component | Status | Verification Method | Notes |
| :--- | :--- | :--- | :--- |
| **Waveform Parser** | ✅ **COMPLETE** | Unit Test (`test_pure_logic.py`) | Logic extracted to static method. Verified with "Golden Master" & Sabotage. |
| **Settings Parser** | ✅ **COMPLETE** | Unit Test (`test_pure_logic.py`) | Verified against real PB980 binary payload. |
| **Config Loader** | ✅ **COMPLETE** | Unit Test (`test_config.py`) | Mocked filesystem. Verified corruption fallback logic. |
| **Database Persistence**| ✅ **COMPLETE** | Integration Test (`test_database.py`) | Verified Schema, WAL mode, and Data Integrity (Real SQLite file). |
| **Reconnection Logic** | ✅ **COMPLETE** | Integration Test (`test_reconnection.py`) | Verified "Self-Healing" from SerialException. Includes safety monitor for worker crashes. |

---

## 2. Updated Architecture (Refactored for Testability)

To enable robust testing without hardware, the monolithic `VentilatorWorker` was refactored. The parsing logic is now **pure functional code** decoupled from the Qt Thread loop.

**Key Changes:**
* `VentilatorWorker.parse_incoming_chunk(buffer, data)` -> Static method. Returns events list.
* `VentilatorWorker.parse_settings_chunk(buffer, data)` -> Static method. Returns messages list.
* `VentilatorWorker.process_waveform_buffer` -> Now acts as an orchestrator/signal-emitter only.

---

## 3. Test Suite Inventory

### Unit Tests (Fast, No Mocks needed)
* `tests/unit/test_pure_logic.py`: Exhaustive testing of the static parsers (fragmentation, overflow, real binary payloads).
* `tests/unit/test_config.py`: Tests user configuration loading, unit conversion, and error handling. (Uses heavy mocking of `VentilatorApp` to avoid GUI crashes).

### Integration Tests (Real Components)
* `tests/integration/test_database.py`: Tests `DatabaseManager` against a real file system. Verifies schema creation, high-volume inserts, and version migration.
* `tests/integration/test_reconnection.py`: Tests the `VentilatorWorker`'s ability to survive a `SerialException` (cable pull) and resume automatically using a Mock Serial Factory.

### Test Fixtures (`tests/conftest.py`)
* **Global Mocks:** `serial`, `wandb`, `ctypes` are mocked at the module level.
* **Fixtures:** `temp_db` (fresh SQLite file per test).

---

## 4. Next Steps (Future Sessions)

1.  **UI "Smoke" Test:** Ensure the application launches without crashing on Windows.
2.  **End-to-End Workflow:** Verify that clicking "Start" actually triggers the worker (connecting the GUI to the backend).
3.  **CI Pipeline:** Finalize `.github/workflows/test_suite.yml` (Script already drafted).