"""Unit tests for ventilation-mode canonicalization (Defect B fix).

These tests pin the behaviour of the pure, module-level
``canonicalize_vent_mode`` helper that maps the composite mode strings the
recorder stores in the SQLite ``vent_mode`` column (e.g. ``"VC A/C"``,
``"VC+ A/C"``, ``"PS SPONT"``, ``"VC+ PS SIMV"``) onto the canonical labels
that ``syncrone-library`` recognises (VCV / PCV / PSV) or a faithful
passthrough for modes the library deliberately does not remap
(SIMV / BiLevel / CPAP).

The mapping table MUST stay in sync with
``syncrone_library/io/__init__.py`` (VENT_MODE_MAP / VENT_MODE_SUBSTRING_MAP).
"""

from typing import ClassVar

import pytest

from main import (
    VENT_MODE_MAP,
    VENT_MODE_SUBSTRING_MAP,
    canonicalize_vent_mode,
)


class TestCanonicalMappedModes:
    """Modes that map to a canonical analysis label (VCV/PCV/PSV)."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # Volume Control A/C -> VCV (both field orders)
            ("VC A/C", "VCV"),
            ("A/C VC", "VCV"),
            # Pressure Control A/C -> PCV (both field orders)
            ("PC A/C", "PCV"),
            ("A/C PC", "PCV"),
            # REGRESSION (Defect B): VC+ is volume-targeted PRESSURE control.
            # The old whitelist mislabelled it as VCV; it must be PCV to match
            # syncrone-library io/__init__.py:23-24.
            ("VC+ A/C", "PCV"),
            ("A/C VC+", "PCV"),
        ],
    )
    def test_mapped_mode(self, raw, expected):
        assert canonicalize_vent_mode(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["PS SPONT", "SPONT PS"],
    )
    def test_spontaneous_pressure_support_maps_to_psv(self, raw):
        # Spontaneous / Pressure Support -> PSV (substring rule,
        # syncrone-library io/__init__.py:30-31).
        assert canonicalize_vent_mode(raw) == "PSV"


class TestPassthroughModes:
    """Modes syncrone-library keeps verbatim (not VCV/PCV/PSV)."""

    @pytest.mark.parametrize(
        "raw",
        [
            "VC PS SIMV",
            "PC PS SIMV",
            "BILEVL",
            "CPAP",
        ],
    )
    def test_unmapped_mode_passes_through_unchanged(self, raw):
        # These are intentionally NOT remapped to VCV/PCV/PSV — the analysis
        # library treats them as ineligible for ineffective-effort scoring
        # (test_csv_io.py:275-277). Remapping them would silently score
        # ineligible breaths, so passthrough is the correct behaviour.
        assert canonicalize_vent_mode(raw) == raw

    def test_vc_plus_simv_preserves_plus_character(self):
        # The old sanitiser stripped '+', collapsing "VC+ PS SIMV" onto
        # "VC PS SIMV" and destroying the volume-vs-volume+ distinction.
        result = canonicalize_vent_mode("VC+ PS SIMV")
        assert "+" in result
        assert result == "VC+ PS SIMV"
        # Must remain distinct from the plain VC SIMV composite.
        assert result != canonicalize_vent_mode("VC PS SIMV")

    def test_slash_preserved_in_passthrough(self):
        # The old sanitiser also stripped '/'. An unmapped A/C-style token
        # must keep its slash rather than becoming "FOO AC".
        assert canonicalize_vent_mode("FOO A/C") == "FOO A/C"


class TestAnnotationSafety:
    """The canonical label is embedded in an EDF annotation as
    ``f"{mode}-{breath_index}"`` and later split on '-' by both the recorder
    and syncrone-library. The mode token must therefore never contain '-' or
    control characters."""

    def test_hyphen_removed_to_protect_delimiter(self):
        result = canonicalize_vent_mode("VC-AC")
        assert "-" not in result

    def test_control_characters_removed(self):
        result = canonicalize_vent_mode("VC\x14A\x15C")
        assert "\x14" not in result
        assert "\x15" not in result


class TestDefaultAndEmptyHandling:
    """Absent / unrecognised-empty inputs degrade to the 'Unknown'
    placeholder (Defect A — the startup/PB840 placeholder — is a separate
    follow-up; here we only preserve the existing default contract)."""

    @pytest.mark.parametrize("raw", [None, "", "   ", "\t\n"])
    def test_missing_mode_returns_unknown(self, raw):
        assert canonicalize_vent_mode(raw) == "Unknown"

    def test_unknown_passthrough(self):
        assert canonicalize_vent_mode("Unknown") == "Unknown"


class TestSyncroneLibraryParity:
    """Documents the KEEP-IN-SYNC contract with
    syncrone_library/io/__init__.py. If the analysis library changes its
    mapping, this test must be updated deliberately (and the ported table in
    main.py with it)."""

    # Verbatim copy of syncrone_library/io/__init__.py:20-32 (VENT_MODE_MAP).
    EXPECTED_MAP: ClassVar[dict[str, str]] = {
        "A/C VC": "VCV",
        "VC A/C": "VCV",
        "A/C VC+": "PCV",
        "VC+ A/C": "PCV",
        "A/C PC": "PCV",
        "PC A/C": "PCV",
    }
    EXPECTED_SUBSTRING: ClassVar[list[tuple[str, str]]] = [
        ("SPONT PS", "PSV"),
        ("PS SPONT", "PSV"),
    ]

    def test_map_matches_library(self):
        assert VENT_MODE_MAP == self.EXPECTED_MAP

    def test_substring_map_matches_library(self):
        assert list(VENT_MODE_SUBSTRING_MAP) == self.EXPECTED_SUBSTRING
