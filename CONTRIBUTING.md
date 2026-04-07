# Contributing to Syncron-E Waveform Recorder

Thank you for your interest in contributing to the Syncron-E Waveform Recorder. This project is a PB980 ventilator waveform recording system used in medical device workflows, so high-quality contributions are essential.

## Getting Started

1. **Clone the repository**
   ```bash
   git clone https://github.com/AHC-Syncron-E/syncrone-logger.git
   cd syncrone-logger
   ```

2. **Install dependencies with test extras**
   ```bash
   pip install -e ".[test]"
   ```

3. **Run the application**
   ```bash
   python main.py
   ```

4. **Run the test suite**
   ```bash
   pytest tests/
   ```

   For headless CI environments, set the Qt platform:
   ```bash
   QT_QPA_PLATFORM=offscreen pytest tests/
   ```

## Development Environment

**Requirements:**
- Windows 11 (mandatory; this is a Windows-only application)
- Python 3.13

**Important notes:**
- PySide6 version must be pinned to **6.10.1** to avoid a memory leak regression in later versions
- `requirements.txt` contains only runtime dependencies and is used for production deployments
- `pyproject.toml` defines the complete dependency graph with separate groups for runtime, test, and build tooling
- Install from `pyproject.toml` for development: `pip install -e ".[test]"`

## Code Style

**Linting:**
- Use ruff for all style checks:
  ```bash
  ruff check .
  ```

**Docstrings:**
- Follow NumPy style docstrings for all classes and public methods
- Document parameters, return values, and exceptions

**Type Hints:**
- Add type hints to all public methods and functions
- Use `from __future__ import annotations` at the top of modules to enable forward references
- Aim for comprehensive coverage: typing should not be optional

**Line Length:**
- Maximum 120 characters per line

## Testing

The test suite is organized into three directories:

- **unit/**: Fast, isolated tests for individual functions and classes
- **integration/**: Tests that verify multiple components working together
- **e2e/**: End-to-end tests that exercise real serial I/O and GUI workflows
- **regression baseline/**: Baseline EDF and SQLite artifacts for regression testing

**Test Infrastructure:**
- `conftest.py` provides fixtures that pre-mock serial port interfaces and PyQtGraph for headless CI runs
- All tests run in headless mode without a display server
- Coverage reports are generated with pytest-cov:
  ```bash
  pytest tests/ --cov=main
  ```

## Pull Request Process

1. **Create a feature branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Write clear commit messages** that explain what changed and why

3. **Ensure CI passes**:
   - All tests must pass: `pytest tests/`
   - Linting must pass: `ruff check .`
   - Type checking is validated as part of CI

4. **Describe your changes in the PR**:
   - What problem does this solve?
   - What changed?
   - **Does this affect the EDF file format or SQLite schema?** If yes, flag this for review—downstream Syncron-E analysis pipelines depend on these formats being stable.

5. **Wait for review** before merging

## Reporting Issues

Use GitHub Issues to report bugs or request features. Include:

- **Version number** (visible in the application title bar or `__version__` in the code)
- **Operating system** (Windows 11 version and build number if possible)
- **Steps to reproduce** the issue
- **Expected vs. actual behavior**
- **Relevant log files or screenshots** if applicable

## Data Output Compatibility

**Critical:** Changes that affect the following require discussion before implementation:

- **EDF+ file structure** (header format, annotations, channel layout)
- **SQLite schema** (table definitions, column types, primary keys)

The downstream Syncron-E analysis platform depends on stable, predictable output formats. If your PR modifies either of these, please:

1. Open a GitHub Issue to discuss the change
2. Explain the motivation and impact
3. Include migration or compatibility notes in the PR description

## Code of Conduct

This project is committed to fostering a welcoming, inclusive environment. Be respectful, professional, and constructive in all interactions.

Thank you for contributing!
