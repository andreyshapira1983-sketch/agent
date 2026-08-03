import pytest
from tools.current_time import format_duration_seconds

def test_format_duration_seconds_formats_common_ranges():
    assert format_duration_seconds(59) == '59s'
    assert format_duration_seconds(60) == '1m 0s'
    assert format_duration_seconds(61) == '1m 1s'
    assert format_duration_seconds(3600) == '1h 0m 0s'
    assert format_duration_seconds(3661) == '1h 1m 1s'
    assert format_duration_seconds(3661.9) == '1h 1m 1s'

def test_format_duration_seconds_rejects_negative_values():
    with pytest.raises(ValueError):
        format_duration_seconds(-1)
