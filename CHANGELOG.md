# Changelog

All notable changes to the Syncron-E Waveform Recorder will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **PB840 settings-frame parsing (PROVISIONAL — to be verified).** Accepts the shorter PB840 `MISCF` response to `SNDF` (assumed 171 fields, vs. 173 on the PB980) so the ventilation mode is logged instead of defaulting to `Unknown`. **Not yet validated against real PB840 hardware** — the field count and the mode/mandatory/spont field positions are assumed from protocol documentation. PB980 mode logging is unaffected. See the "PB840/PB980 Compatibility" section in the README.

### Fixed
- **EDF snapshot data loss (C1):** no longer glob-deletes every `*.edf` in the output folder; writes atomically (temp → rename) and removes only the session's own previous snapshot, after a successful write.
- **EDF start time (C2):** derived from the first sample in the window instead of a hardcoded `now - 1h`, fixing misdated sub-hour captures.
- **Data loss on stop (H1):** `DatabaseManager.close()` now commits the pending batch (and logs a failed final commit instead of silently swallowing it).
- **Missing `edfio` (H2):** surfaced at startup instead of silently producing no EDF files.
- **Repeated EDF export failures (H3):** now alert the UI after 3 consecutive failures instead of only logging to file.
- **Settings-frame robustness:** require the `MISCF` header token before the field-count check, preventing a head-truncated frame (e.g. after a reconnect that flushes the input buffer mid-frame) from logging a scrambled ventilation mode on the PB980.
- **Unknown-mode warning:** now clears on the first successfully parsed mode instead of remaining stuck on screen.

## [1.4.2] - 2026-04-06

### Changed
- Replaced bare `except:` clauses with specific exception types for improved debuggability
- Added comprehensive type hints (95% return types, 88% argument types) across all classes
- Added NumPy-style docstrings to all classes and public methods
- Migrated project configuration to `pyproject.toml` with separated runtime/test/build dependency groups

### Removed
- Removed dead dependencies: `wandb`, `psutil`
- Removed stale files: `environment.yml`, `tests/integration/test_worker_state.py`

### Fixed
- Fixed patient ID sanitization: spaces now replaced with underscores instead of being silently dropped
- Fixed CI test failures from stale `TelemetryManager` mock and missing `edfio` dependency
- Fixed `test_regression_baseline.py` to use `parse_incoming_chunk` static method

## [1.4.1] - 2026-04-06

### Changed
- Repository converted from private to public under MIT license
- Removed embedded debug terminal, hardcoded debug PIN, and development artifacts
- Scrubbed git history of API keys, device serial numbers, and internal paths
- Added `SECURITY.md` responsible disclosure policy
- Added `CODEOWNERS` for branch ruleset enforcement
- Added GitHub Actions CI test suite and MSIX build pipelines

## [1.0.0] - 2026-01-15

### Added
- Initial release of Syncron-E Waveform Recorder
- Dual serial port capture (waveform @ 38400 bps, settings @ 9600 bps)
- Real-time PyQtGraph visualization with breath markers
- SQLite WAL-mode database with batch insert for high-fidelity storage
- Rolling 1-hour EDF+ file export with breath boundary annotations
- Automatic serial reconnection on cable disconnect
- Configurable auto-stop rules (time-based and breath-count-based)
- Auto-lock safety feature with inactivity timeout
- Signed MSIX deployment via Azure Trusted Signing
