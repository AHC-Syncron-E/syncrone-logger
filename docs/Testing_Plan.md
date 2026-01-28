# Syncron-E Waveform Recorder - Automated Test Plan

**Version:** 1.0  
**Date:** January 28, 2026  
**Application Version:** 1.2.3  
**Status:** Planning / Draft for Team Review

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Testing Philosophy & Goals](#2-testing-philosophy--goals)
3. [Architecture Overview](#3-architecture-overview)
4. [Test Categories](#4-test-categories)
5. [Detailed Test Cases by Component](#5-detailed-test-cases-by-component)
6. [Mocking & Fixture Strategy](#6-mocking--fixture-strategy)
7. [CI/CD Integration Plan](#7-cicd-integration-plan)
8. [Coverage Goals & Metrics](#8-coverage-goals--metrics)
9. [Risk Assessment](#9-risk-assessment)
10. [Implementation Roadmap](#10-implementation-roadmap)
11. [Open Questions for Team Discussion](#11-open-questions-for-team-discussion)

---

## 1. Executive Summary

This document outlines the automated testing strategy for the Syncron-E Waveform Recorder application. The primary objective is to establish a robust regression testing framework that:

- Prevents regressions as new features are added
- Validates critical data integrity paths (patient data recording)
- Enables confident refactoring and maintenance
- Integrates seamlessly with GitHub Actions CI/CD pipeline

**Key Constraints:**
- Application uses PySide6 (Qt) for GUI - requires specialized testing approach
- Serial communication with medical devices - requires comprehensive mocking
- Real-time data streaming - timing-sensitive operations need careful test design
- File I/O and SQLite database operations - need isolation strategies

---

## 2. Testing Philosophy & Goals

### 2.1 Testing Pyramid

We recommend following the standard testing pyramid:

```
        /\
       /  \      E2E Tests (5-10%)
      /----\     - Full workflow validation
     /      \    - Critical user journeys
    /--------\   Integration Tests (20-30%)
   /          \  - Component interactions
  /------------\ - Database + Worker integration
 /              \
/----------------\  Unit Tests (60-70%)
                    - Isolated component logic
                    - Pure functions
                    - Data parsing/validation
```

### 2.2 Primary Goals

| Goal | Priority | Rationale |
|------|----------|-----------|
| Data Integrity | **Critical** | Patient waveform data must never be lost or corrupted |
| Regression Prevention | **High** | New features must not break existing functionality |
| Parser Accuracy | **High** | Waveform/settings parsing must remain accurate |
| Reconnection Reliability | **High** | Self-healing logic is safety-critical |
| Configuration Stability | **Medium** | Config loading/migration must be robust |
| UI State Consistency | **Medium** | UI should reflect accurate system state |

### 2.3 What We Will NOT Test (Initially)

- Visual appearance/styling (manual QA)
- Actual serial hardware communication (use mocks)
- Performance benchmarking (separate effort)
- EmbeddedTerminal/debugger functionality (low priority, security-gated)

---

## 3. Architecture Overview

### 3.1 Component Dependency Graph

```
┌─────────────────────────────────────────────────────────────────┐
│                        VentilatorApp                            │
│                      (Main Window / UI)                         │
└─────────────────┬───────────────────────────────┬───────────────┘
                  │                               │
                  ▼                               ▼
┌─────────────────────────────┐   ┌─────────────────────────────┐
│     VentilatorWorker        │   │     SnapshotWorker          │
│   (Data Acquisition Thread) │   │   (Periodic File Export)    │
└──────────┬──────────────────┘   └──────────────┬──────────────┘
           │                                      │
           ▼                                      │
┌─────────────────────────────┐                   │
│     DatabaseManager         │◄──────────────────┘
│       (SQLite WAL)          │
└─────────────────────────────┘
           │
           ▼
┌─────────────────────────────┐
│    File System (Raw Logs)   │
│  - waveforms_*.txt          │
│  - settings_*.txt           │
└─────────────────────────────┘
```

### 3.2 Testable Units Identified

| Component | Type | Testability | Notes |
|-----------|------|-------------|-------|
| `DatabaseManager` | Class | High | Pure logic, easy to isolate |
| `BreathMarker` | Class | Medium | Depends on pyqtgraph objects |
| `BreathMarkerManager` | Class | Medium | Manages marker lifecycle |
| `SnapshotWorker` | QThread | Medium | Needs DB + filesystem mocks |
| `VentilatorWorker` | QThread | Low-Medium | Heavy mocking required |
| `VentilatorApp` | QMainWindow | Low | GUI testing complexity |
| Config loading (`_process_options`) | Method | High | Pure transformation logic |
| Waveform parsing (`process_waveform_buffer`) | Method | High | Critical, isolated logic |
| Settings parsing (`process_settings_buffer`) | Method | High | Critical, isolated logic |

---

## 4. Test Categories

### 4.1 Unit Tests

**Purpose:** Test individual functions/methods in isolation.

**Scope:**
- Data parsing functions
- Configuration processing
- Database operations (with in-memory SQLite)
- Time/duration calculations
- Disk space calculations

**Framework:** `pytest` with `pytest-mock`

### 4.2 Integration Tests

**Purpose:** Test component interactions.

**Scope:**
- Worker → Database flow
- Worker → File system flow
- Config load → UI state
- Signal/slot connections (Qt)

**Framework:** `pytest` with `pytest-qt`

### 4.3 End-to-End Tests

**Purpose:** Validate complete user workflows.

**Scope:**
- Start recording → receive data → stop recording
- Connection loss → reconnection → resume
- Auto-stop triggers (time/breath limits)

**Framework:** `pytest-qt` with comprehensive mocking

### 4.4 Regression Tests

**Purpose:** Specific tests for previously identified bugs.

**Scope:**
- Created as bugs are discovered and fixed
- Each regression test documents the original issue

---

## 5. Detailed Test Cases by Component

### 5.1 DatabaseManager

#### 5.1.1 Connection & Initialization

| Test ID | Test Case | Expected Behavior | Priority |
|---------|-----------|-------------------|----------|
| DB-001 | Create new database on first run | Database file created, tables exist | High |
| DB-002 | Connect to existing database | Connection succeeds, no data loss | High |
| DB-003 | Parent directory doesn't exist | Directory created automatically | Medium |
| DB-004 | Database file is corrupted/locked | Graceful error handling | Medium |

#### 5.1.2 Schema & Migration

| Test ID | Test Case | Expected Behavior | Priority |
|---------|-----------|-------------------|----------|
| DB-010 | `_needs_migration()` with old schema | Returns `True` | High |
| DB-011 | `_needs_migration()` with current schema | Returns `False` | High |
| DB-012 | `_backup_and_reset()` preserves old data | Backup file created with timestamp | High |
| DB-013 | Table creation idempotency | Running twice doesn't error | Medium |

#### 5.1.3 Data Operations

| Test ID | Test Case | Expected Behavior | Priority |
|---------|-----------|-------------------|----------|
| DB-020 | `insert_waveform()` with all fields | Row inserted with correct values | Critical |
| DB-021 | `insert_waveform()` with NULL pressure/flow | Row inserted, NULLs preserved | High |
| DB-022 | `insert_setting()` basic insert | Row inserted with timestamp | High |
| DB-023 | `commit_batch()` persists pending writes | Data survives connection close | Critical |
| DB-024 | High-volume inserts (1000+ rows) | No errors, acceptable performance | High |
| DB-025 | Concurrent access (multi-thread) | WAL mode handles correctly | Medium |

---

### 5.2 Waveform Parsing (`process_waveform_buffer`)

#### 5.2.1 Valid Input Parsing

| Test ID | Test Case | Input | Expected Output | Priority |
|---------|-----------|-------|-----------------|----------|
| WF-001 | Single valid data line | `"10.5,25.3\n"` | Signal emitted: (25.3, 10.5) | Critical |
| WF-002 | Multiple data lines | `"10,20\n15,25\n"` | Two signals emitted | Critical |
| WF-003 | Breath start marker | `"BS, S:1234,\n"` | `sig_breath_seq("1234")` emitted | Critical |
| WF-004 | Breath end marker | `"BE\n"` | No data signal, line skipped | High |
| WF-005 | Mixed content | `"BS, S:1,\n10,20\nBE\n"` | Correct sequence of signals | Critical |

#### 5.2.2 Edge Cases & Error Handling

| Test ID | Test Case | Input | Expected Behavior | Priority |
|---------|-----------|-------|-------------------|----------|
| WF-010 | Empty string | `""` | No signals, no errors | High |
| WF-011 | Partial line (no newline) | `"10,20"` | Buffered, not processed yet | High |
| WF-012 | Partial then complete | `"10,20"` then `"\n"` | Signal emitted on second call | High |
| WF-013 | Malformed data (non-numeric) | `"abc,def\n"` | Line skipped, no crash | High |
| WF-014 | Wrong number of fields | `"10,20,30\n"` | Line skipped | Medium |
| WF-015 | Buffer overflow protection | 10KB of garbage | Buffer cleared, no OOM | High |
| WF-016 | Unicode/special characters | `"10,20\x00\n"` | Handled gracefully | Medium |
| WF-017 | Very large numbers | `"999999.99,999999.99\n"` | Parsed correctly | Medium |
| WF-018 | Negative numbers | `"-10.5,-25.3\n"` | Parsed correctly | High |
| WF-019 | Scientific notation | `"1e2,2e3\n"` | Parsed or gracefully skipped | Low |

#### 5.2.3 Breath Sequence Pattern Matching

| Test ID | Test Case | Input | Expected Match | Priority |
|---------|-----------|-------|----------------|----------|
| WF-030 | Standard format | `"BS, S:12345,"` | Group 1 = "12345" | Critical |
| WF-031 | No space after comma | `"BS,S:12345,"` | Should still match? | **Discuss** |
| WF-032 | Extra whitespace | `"BS,  S:12345,"` | Should match | Medium |
| WF-033 | Leading zeros | `"BS, S:00001,"` | Group 1 = "00001" | Medium |

---

### 5.3 Settings Parsing (`process_settings_buffer`)

| Test ID | Test Case | Input | Expected Output | Priority |
|---------|-----------|-------|-----------------|----------|
| ST-001 | Valid settings line (173+ fields) | CSV with mode at [7,8,9] | `sig_settings_msg` with mode | High |
| ST-002 | Incomplete line (<173 fields) | Short CSV | No signal emitted | High |
| ST-003 | Carriage return delimiter | `"a,b,...\r"` | Processed correctly | High |
| ST-004 | Buffer overflow protection | 10KB of garbage | Buffer cleared | High |
| ST-005 | Empty fields in mode positions | `"...,,,..."` | Graceful handling | Medium |

---

### 5.4 Configuration System

#### 5.4.1 Config Loading (`load_config`)

| Test ID | Test Case | Expected Behavior | Priority |
|---------|-----------|-------------------|----------|
| CFG-001 | Config file doesn't exist | Defaults created and saved | High |
| CFG-002 | Valid config file | Options loaded correctly | High |
| CFG-003 | Corrupted JSON | Backup created, defaults restored | High |
| CFG-004 | Missing required keys | Error detected, defaults restored | High |
| CFG-005 | Empty options array | Error detected, defaults restored | Medium |

#### 5.4.2 Options Processing (`_process_options`)

| Test ID | Test Case | Input | Expected Value | Priority |
|---------|-----------|-------|----------------|----------|
| CFG-020 | Time in seconds | `{"type":"time", "value":60, "unit":"seconds"}` | 60 | High |
| CFG-021 | Time in minutes | `{"type":"time", "value":5, "unit":"minutes"}` | 300 | High |
| CFG-022 | Time in hours | `{"type":"time", "value":2, "unit":"hours"}` | 7200 | High |
| CFG-023 | Time in days | `{"type":"time", "value":1, "unit":"days"}` | 86400 | High |
| CFG-024 | Time in weeks | `{"type":"time", "value":1, "unit":"weeks"}` | 604800 | High |
| CFG-025 | Breaths type (no conversion) | `{"type":"breaths", "value":5000, "unit":"breaths"}` | 5000 | High |
| CFG-026 | Manual type | `{"type":"manual", "value":0, "unit":"none"}` | 0 | Medium |
| CFG-027 | Unknown unit | `{"type":"time", "value":1, "unit":"fortnights"}` | ValueError raised | High |
| CFG-028 | Case insensitive units | `{"unit":"HOURS"}` | Handled correctly | Medium |

---

### 5.5 VentilatorWorker

#### 5.5.1 Port Detection

| Test ID | Test Case | Mock Setup | Expected Behavior | Priority |
|---------|-----------|------------|-------------------|----------|
| VW-001 | Two valid cables found | Mock 2 FTDI devices | Ports opened successfully | Critical |
| VW-002 | Only one cable found | Mock 1 FTDI device | Error signal emitted | High |
| VW-003 | No cables found | Mock 0 devices | Error signal emitted | High |
| VW-004 | Three cables found | Mock 3 FTDI devices | First two used (sorted) | Medium |
| VW-005 | Mixed VID/PID devices | Mock FTDI + PL2303 | Both recognized | High |

#### 5.5.2 Port Identification

| Test ID | Test Case | Mock Data | Expected Behavior | Priority |
|---------|-----------|-----------|-------------------|----------|
| VW-010 | Waveform on Port A | Port A sends `"BS, S:1,"` | Port A = waveform, Port B = settings | Critical |
| VW-011 | Waveform on Port B | Port B sends `"BS, S:1,"` | Port B = waveform, Port A = settings | Critical |
| VW-012 | Timeout before identification | No waveform pattern | Continues waiting | Medium |
| VW-013 | Both ports send waveform pattern | Race condition | First detected wins | Low |

#### 5.5.3 Reconnection Logic

| Test ID | Test Case | Scenario | Expected Behavior | Priority |
|---------|-----------|----------|-------------------|----------|
| VW-020 | Connection lost, quick recovery | Disconnect, reconnect in 5s | `sig_connection_restored` emitted | Critical |
| VW-021 | Connection lost, timeout exceeded | Disconnect, no reconnect for 120s | Error signal, worker stops | Critical |
| VW-022 | Connection lost during identification | Disconnect before ports identified | Reconnect restarts identification | High |
| VW-023 | Multiple reconnection cycles | Disconnect/reconnect 3 times | All cycles handled | High |
| VW-024 | Partial reconnect (1 of 2 cables) | Only 1 cable available | Continues waiting | Medium |

#### 5.5.4 File Operations

| Test ID | Test Case | Expected Behavior | Priority |
|---------|-----------|-------------------|----------|
| VW-030 | Log file creation | Files created in correct directory | High |
| VW-031 | File rotation at midnight | Old files closed, new files opened | High |
| VW-032 | Write failure (disk full) | Graceful handling, no crash | High |
| VW-033 | `fsync` after writes | Data persisted to disk | Critical |

#### 5.5.5 Signal Throttling

| Test ID | Test Case | Expected Behavior | Priority |
|---------|-----------|-------------------|----------|
| VW-040 | RX activity throttling | Max 10 signals/second per port | Medium |
| VW-041 | High data rate | No signal queue backup | Medium |

---

### 5.6 SnapshotWorker

| Test ID | Test Case | Expected Behavior | Priority |
|---------|-----------|-------------------|----------|
| SS-001 | Generate files after 5 minutes | Files created in output folder | High |
| SS-002 | Query only last 1 hour of data | Cutoff timestamp correct | High |
| SS-003 | Streaming prevents RAM spike | Memory usage bounded | High |
| SS-004 | Atomic file writes (temp → final) | No partial files visible | High |
| SS-005 | Empty result set | No file created (or empty file?) | Medium |
| SS-006 | Worker shutdown during generation | Clean exit, no corruption | High |

---

### 5.7 VentilatorApp (UI State Machine)

#### 5.7.1 State Transitions

| Test ID | Test Case | Action | Expected State | Priority |
|---------|-----------|--------|----------------|----------|
| UI-001 | Initial state | App launch | Ready, inputs enabled | High |
| UI-002 | Start recording | Click start | Logging, inputs disabled | High |
| UI-003 | Stop recording | Click stop | Ready, inputs enabled | High |
| UI-004 | Lock while recording | Click lock | Locked, stop disabled | High |
| UI-005 | Unlock while recording | Click unlock | Recording, stop enabled | High |
| UI-006 | Close while recording | Click X | Blocked with warning | High |
| UI-007 | Close while locked | Click X | Blocked with warning | High |

#### 5.7.2 Auto-Stop Triggers

| Test ID | Test Case | Setup | Expected Behavior | Priority |
|---------|-----------|-------|-------------------|----------|
| UI-020 | Time limit reached | 1-hour rule, 3600s elapsed | Recording stops automatically | Critical |
| UI-021 | Breath limit reached | 2000 breaths rule, 2000 counted | Recording stops automatically | Critical |
| UI-022 | Disk space critical | <500MB free | Recording stops, warning shown | Critical |
| UI-023 | Manual mode | No auto-stop | Recording continues indefinitely | High |

#### 5.7.3 Duration Tracking

| Test ID | Test Case | Scenario | Expected Display | Priority |
|---------|-----------|----------|------------------|----------|
| UI-030 | Normal recording | 1h 30m elapsed | "01:30:00" | High |
| UI-031 | Pause during reconnect | 1h recording, 5min disconnect | ~"01:00:00" (excludes disconnect) | High |
| UI-032 | Multiple reconnections | 3 disconnects of 1min each | Total excludes all 3 minutes | High |

---

### 5.8 BreathMarkerManager

| Test ID | Test Case | Expected Behavior | Priority |
|---------|-----------|-------------------|----------|
| BM-001 | Add new marker | Marker appears at x=-0.02 | Medium |
| BM-002 | Add duplicate marker | No duplicate created | Medium |
| BM-003 | Shift all markers | All markers move by step size | Medium |
| BM-004 | Remove expired markers | Markers at x<-10 removed | Medium |
| BM-005 | Cleanup on destroy | pyqtgraph items removed | Medium |

---

## 6. Mocking & Fixture Strategy

### 6.1 Serial Port Mocking

```python
# Proposed fixture structure
@pytest.fixture
def mock_serial_ports(mocker):
    """
    Mock serial.tools.list_ports.comports() and serial.Serial
    """
    # Create virtual port objects with VID/PID
    # Allow injection of data to simulate device output
    pass
```

**Key Mocking Points:**
- `serial.tools.list_ports.comports()` - Return configurable device list
- `serial.Serial` - Return mock object with `read()`, `write()`, `in_waiting`
- Simulate connection loss via `SerialException`

### 6.2 Database Fixtures

```python
@pytest.fixture
def temp_database(tmp_path):
    """
    Provide isolated in-memory or temp file database
    """
    db_path = tmp_path / "test.db"
    manager = DatabaseManager(str(db_path))
    manager.connect()
    yield manager
    manager.close()
```

### 6.3 Qt Application Fixture

```python
@pytest.fixture(scope="session")
def qapp():
    """
    Single QApplication instance for all Qt tests
    """
    app = QApplication.instance() or QApplication([])
    yield app
```

### 6.4 File System Isolation

- Use `pytest`'s `tmp_path` fixture for all file operations
- Never write to real user directories during tests
- Use `monkeypatch` to override `Path.home()` if needed

### 6.5 Time Mocking

```python
@pytest.fixture
def mock_time(mocker):
    """
    Control time.monotonic() and datetime.now() for timing tests
    """
    pass
```

---

## 7. CI/CD Integration Plan

### 7.1 GitHub Actions Workflow Structure

```yaml
# .github/workflows/test.yml (proposed structure)
name: Test Suite

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install -r requirements-test.txt
      - name: Run unit tests
        run: pytest tests/unit -v --cov=syncrone --cov-report=xml
      - name: Upload coverage
        uses: codecov/codecov-action@v4

  integration-tests:
    runs-on: ubuntu-latest
    needs: unit-tests
    steps:
      - name: Install Qt dependencies
        run: sudo apt-get install -y libxcb-xinerama0 libxkbcommon-x11-0
      - name: Run integration tests
        run: |
          export QT_QPA_PLATFORM=offscreen
          pytest tests/integration -v
```

### 7.2 Test Directory Structure

```
syncrone-logger/
├── main.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # Shared fixtures
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_database.py
│   │   ├── test_waveform_parser.py
│   │   ├── test_settings_parser.py
│   │   ├── test_config.py
│   │   └── test_markers.py
│   ├── integration/
│   │   ├── __init__.py
│   │   ├── test_worker_database.py
│   │   ├── test_snapshot_worker.py
│   │   └── test_ui_states.py
│   └── e2e/
│       ├── __init__.py
│       └── test_recording_workflow.py
├── requirements.txt
└── requirements-test.txt
```

### 7.3 Required Test Dependencies

```
# requirements-test.txt
pytest>=8.0.0
pytest-qt>=4.3.0
pytest-cov>=4.1.0
pytest-mock>=3.12.0
pytest-timeout>=2.2.0
pytest-xdist>=3.5.0  # Parallel execution (optional)
```

### 7.4 CI Performance Considerations

| Concern | Mitigation |
|---------|------------|
| Qt tests slow on CI | Use `QT_QPA_PLATFORM=offscreen` |
| Flaky timing tests | Use time mocking, avoid `time.sleep()` |
| Database lock contention | Use separate temp directories per test |
| Long test suite | Parallelize with `pytest-xdist` |

---

## 8. Coverage Goals & Metrics

### 8.1 Target Coverage by Component

| Component | Target Line Coverage | Rationale |
|-----------|---------------------|-----------|
| `DatabaseManager` | 90% | Critical data path |
| Waveform parsing | 95% | Critical data integrity |
| Settings parsing | 90% | Important but simpler |
| Config loading | 85% | Error handling important |
| `VentilatorWorker` | 70% | Complex, some paths hard to test |
| `VentilatorApp` | 60% | GUI code harder to cover |
| Marker classes | 70% | Moderate importance |
| **Overall Target** | **75%** | Realistic initial goal |

### 8.2 Coverage Exclusions

Lines that may be reasonably excluded from coverage:
- `if __name__ == "__main__":` block
- `EmbeddedTerminal` (security/debug tool)
- Exception logging in catch blocks (hard to trigger)
- Platform-specific code (`ctypes.windll`)

### 8.3 Quality Gates

Proposed PR merge requirements:
- All tests pass
- No decrease in overall coverage
- New code has >80% coverage
- No new `# pragma: no cover` without justification

---

## 9. Risk Assessment

### 9.1 Testing Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Qt tests flaky on CI | Medium | Medium | Offscreen rendering, timeouts |
| Time-based tests unreliable | High | Medium | Mock all time functions |
| Serial mock doesn't match real behavior | Medium | High | Validate mocks against real devices |
| Database tests leave state | Low | Low | Use temp directories, cleanup fixtures |
| Coverage metrics misleading | Medium | Low | Focus on critical paths, not just numbers |

### 9.2 Untestable Areas

Some areas are inherently difficult to automate:
- **Actual serial device communication** - Requires hardware
- **Real-time performance** - Needs dedicated benchmark suite
- **Visual rendering correctness** - Manual QA or screenshot comparison
- **Windows-specific sleep prevention** - Platform-dependent

---

## 10. Implementation Roadmap

### Phase 1: Foundation (Week 1-2)
- [ ] Set up test directory structure
- [ ] Create `conftest.py` with core fixtures
- [ ] Implement `DatabaseManager` unit tests
- [ ] Implement waveform parser unit tests
- [ ] Set up GitHub Actions workflow (unit tests only)

### Phase 2: Core Coverage (Week 3-4)
- [ ] Implement settings parser tests
- [ ] Implement config loading tests
- [ ] Add `VentilatorWorker` port detection tests
- [ ] Add basic UI state tests with `pytest-qt`
- [ ] Enable coverage reporting in CI

### Phase 3: Integration (Week 5-6)
- [ ] Worker → Database integration tests
- [ ] SnapshotWorker tests
- [ ] Reconnection logic tests
- [ ] Auto-stop trigger tests
- [ ] Add integration tests to CI

### Phase 4: Polish (Week 7-8)
- [ ] E2E workflow tests
- [ ] Performance baseline tests (optional)
- [ ] Documentation and test maintenance guide
- [ ] Coverage threshold enforcement

---

## 11. Open Questions for Team Discussion

### 11.1 Architecture Questions

1. **Should we refactor for testability?**
   - Current code has tight coupling between UI and workers
   - Consider extracting parsing logic into separate pure functions
   - Consider dependency injection for database and file operations

2. **How do we handle the embedded debugger (`EmbeddedTerminal`)?**
   - Skip testing entirely?
   - Basic smoke test?
   - Security considerations?

### 11.2 Coverage Questions

3. **What is our minimum acceptable coverage threshold?**
   - Proposed: 75% overall, 90% for data handling
   - Should we block PRs below threshold?

4. **Should we measure branch coverage or just line coverage?**
   - Branch coverage is more rigorous but harder to achieve
   - Start with line, evolve to branch?

### 11.3 CI/CD Questions

5. **How long is acceptable for CI test runs?**
   - Target: < 5 minutes for unit tests
   - Target: < 15 minutes total including integration

6. **Should we run tests on Windows CI runners?**
   - Real target platform is Windows
   - More expensive, but catches platform-specific issues
   - Proposed: Linux for speed, Windows nightly

7. **Do we need hardware-in-the-loop testing?**
   - Dedicated test rig with real ventilator?
   - Manual testing protocol instead?

### 11.4 Process Questions

8. **How do we handle flaky tests?**
   - Quarantine system?
   - Immediate fix requirement?
   - Skip with issue link?

9. **Who is responsible for maintaining tests?**
   - Feature author writes tests?
   - Dedicated test maintenance rotation?

10. **How do we document test coverage of requirements?**
    - Traceability matrix?
    - Test naming conventions?

---

## Appendix A: Regex Pattern Documentation

For reference, the waveform pattern used in the application:

```python
waveform_pattern = re.compile(r"BS,\s*S:(\d+),")
```

| Example Input | Match? | Captured Group |
|---------------|--------|----------------|
| `"BS, S:12345,"` | ✓ | `"12345"` |
| `"BS,S:12345,"` | ✓ | `"12345"` |
| `"BS,  S:12345,"` | ✓ | `"12345"` |
| `"BS, S:12345"` | ✗ | (trailing comma required) |
| `"bs, s:12345,"` | ✗ | (case sensitive) |

---

## Appendix B: Data Flow Diagram for Testing

```
                                    ┌─────────────────┐
                                    │  Serial Device  │
                                    │   (MOCKED)      │
                                    └────────┬────────┘
                                             │
                                             ▼
┌────────────────────────────────────────────────────────────────────┐
│                        VentilatorWorker                            │
│  ┌─────────────┐    ┌─────────────────┐    ┌─────────────────┐    │
│  │ Port Reader │───▶│ process_waveform│───▶│ sig_waveform_   │    │
│  │             │    │ _buffer()       │    │ data.emit()     │    │
│  └─────────────┘    └────────┬────────┘    └─────────────────┘    │
│                              │                                     │
│                              ▼                                     │
│                     ┌─────────────────┐                            │
│                     │ DatabaseManager │                            │
│                     │ .insert_waveform│                            │
│                     └─────────────────┘                            │
└────────────────────────────────────────────────────────────────────┘
                                             │
                         ┌───────────────────┼───────────────────┐
                         │                   │                   │
                         ▼                   ▼                   ▼
                  ┌────────────┐      ┌────────────┐      ┌────────────┐
                  │ Raw Files  │      │  SQLite DB │      │  UI Queue  │
                  │ (MOCKED)   │      │ (IN-MEMORY)│      │ (VERIFY)   │
                  └────────────┘      └────────────┘      └────────────┘

Test Points:
  ★ Input: Mock serial data injection
  ★ Output: Verify DB contents, file contents, signal emissions
```

---

**Next Steps:**
1. Circulate this document to all stakeholders
2. Schedule review meeting to discuss open questions
3. Prioritize and finalize Phase 1 scope
4. Begin implementation once plan is approved