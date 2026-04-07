# Changelog

All notable changes to the Syncron-E Waveform Recorder will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
