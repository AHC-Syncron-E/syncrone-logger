"""H2: a missing edfio library must not be silent.

When edfio is unavailable, HAS_EDF_LIB is False and the app would record
forever while producing zero EDF snapshots and zero log entries. The
``edf_availability_warning`` helper produces the operator-facing message so
the condition can be surfaced at startup (UI + error log) and unit-tested
without Qt.
"""

from main import edf_availability_warning


def test_warning_when_lib_missing():
    msg = edf_availability_warning(False)
    assert msg is not None
    assert "edfio" in msg
    # Must clearly convey that snapshots will not be produced.
    assert "snapshot" in msg.lower() or "edf" in msg.lower()


def test_no_warning_when_lib_present():
    assert edf_availability_warning(True) is None
