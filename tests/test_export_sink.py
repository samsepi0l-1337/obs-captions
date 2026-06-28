"""Tests for obs_captions.export_sink — TranscriptExportSink and pure formatters."""
from __future__ import annotations

from obs_captions.export_sink import TranscriptExportSink, format_srt_cue, format_vtt_cue
from obs_captions.stt.base import Transcript


# ---------------------------------------------------------------------------
# Pure formatter: format_srt_cue
# ---------------------------------------------------------------------------


def test_format_srt_cue_basic():
    result = format_srt_cue(1, 0, 2000, "hello")
    assert result == "1\n00:00:00,000 --> 00:00:02,000\nhello\n"


def test_format_srt_cue_hours():
    result = format_srt_cue(2, 3661000, 3663500, "world")
    assert result == "2\n01:01:01,000 --> 01:01:03,500\nworld\n"


def test_format_srt_cue_index_in_first_line():
    result = format_srt_cue(42, 1500, 3000, "test")
    lines = result.splitlines()
    assert lines[0] == "42"


def test_format_srt_cue_uses_comma_separator():
    result = format_srt_cue(1, 500, 1500, "hi")
    timecode_line = result.splitlines()[1]
    assert "," in timecode_line
    assert "." not in timecode_line


def test_format_srt_cue_milliseconds_zero_padded():
    result = format_srt_cue(1, 5, 995, "x")
    assert "00:00:00,005 --> 00:00:00,995" in result


# ---------------------------------------------------------------------------
# Pure formatter: format_vtt_cue
# ---------------------------------------------------------------------------


def test_format_vtt_cue_basic():
    result = format_vtt_cue(1, 0, 2000, "hello")
    assert result == "1\n00:00:00.000 --> 00:00:02.000\nhello\n"


def test_format_vtt_cue_uses_dot_separator():
    result = format_vtt_cue(1, 500, 1500, "test")
    timecode_line = result.splitlines()[1]
    assert "." in timecode_line
    assert "," not in timecode_line


def test_format_vtt_cue_hours():
    result = format_vtt_cue(3, 7322000, 7324000, "late")
    assert "02:02:02.000 --> 02:02:04.000" in result


def test_format_vtt_cue_index_and_text():
    result = format_vtt_cue(7, 0, 1000, "caption text")
    lines = result.splitlines()
    assert lines[0] == "7"
    assert lines[2] == "caption text"


# ---------------------------------------------------------------------------
# TranscriptExportSink — txt format
# ---------------------------------------------------------------------------


def test_sink_txt_writes_lines(tmp_path):
    path = tmp_path / "out.txt"
    clock_val = [0.0]

    sink = TranscriptExportSink(str(path), "txt", clock=lambda: clock_val[0])
    sink.start()
    sink.on_final(Transcript(text="hello", is_final=True))
    clock_val[0] = 1.0
    sink.on_final(Transcript(text="world", is_final=True))
    sink.stop()

    content = path.read_text(encoding="utf-8")
    assert "hello\n" in content
    assert "world\n" in content


def test_sink_txt_no_webvtt_header(tmp_path):
    path = tmp_path / "out.txt"
    sink = TranscriptExportSink(str(path), "txt", clock=lambda: 0.0)
    sink.start()
    sink.on_final(Transcript(text="hi", is_final=True))
    sink.stop()

    content = path.read_text(encoding="utf-8")
    assert "WEBVTT" not in content


# ---------------------------------------------------------------------------
# TranscriptExportSink — srt format
# ---------------------------------------------------------------------------


def test_sink_srt_uses_transcript_timestamps(tmp_path):
    path = tmp_path / "out.srt"
    sink = TranscriptExportSink(str(path), "srt", clock=lambda: 0.0)
    sink.start()
    t = Transcript(text="hello", is_final=True, start_ms=1000, end_ms=3000)
    sink.on_final(t)
    sink.stop()

    content = path.read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:03,000" in content
    assert "hello" in content


def test_sink_srt_fallback_clock_when_no_timestamps(tmp_path):
    path = tmp_path / "out.srt"
    clock_val = [0.0]

    sink = TranscriptExportSink(str(path), "srt", clock=lambda: clock_val[0])
    sink.start()  # start_time = 0.0, _prev_end_ms = 0
    clock_val[0] = 2.0  # 2 s elapsed
    sink.on_final(Transcript(text="fallback", is_final=True))
    sink.stop()

    content = path.read_text(encoding="utf-8")
    assert "fallback" in content
    # start_ms = _prev_end_ms (0); end_ms = elapsed (2000 ms)
    assert "00:00:00,000 --> 00:00:02,000" in content


def test_sink_srt_multiple_cues_numbered(tmp_path):
    path = tmp_path / "out.srt"
    sink = TranscriptExportSink(str(path), "srt", clock=lambda: 0.0)
    sink.start()
    sink.on_final(Transcript(text="first", is_final=True, start_ms=0, end_ms=1000))
    sink.on_final(Transcript(text="second", is_final=True, start_ms=1000, end_ms=2000))
    sink.stop()

    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    # First cue index is "1", second is "2"
    assert lines[0] == "1"
    # Find "second" cue — should be index "2"
    assert "2" in content
    assert "first" in content
    assert "second" in content


# ---------------------------------------------------------------------------
# TranscriptExportSink — vtt format
# ---------------------------------------------------------------------------


def test_sink_vtt_writes_webvtt_header(tmp_path):
    path = tmp_path / "out.vtt"
    sink = TranscriptExportSink(str(path), "vtt", clock=lambda: 0.0)
    sink.start()
    sink.on_final(Transcript(text="hi", is_final=True, start_ms=0, end_ms=1000))
    sink.stop()

    content = path.read_text(encoding="utf-8")
    assert content.startswith("WEBVTT")


def test_sink_vtt_cue_uses_dot_separator(tmp_path):
    path = tmp_path / "out.vtt"
    sink = TranscriptExportSink(str(path), "vtt", clock=lambda: 0.0)
    sink.start()
    t = Transcript(text="hello", is_final=True, start_ms=500, end_ms=1500)
    sink.on_final(t)
    sink.stop()

    content = path.read_text(encoding="utf-8")
    assert "00:00:00.500 --> 00:00:01.500" in content


def test_sink_vtt_fallback_clock(tmp_path):
    path = tmp_path / "out.vtt"
    clock_val = [0.0]

    sink = TranscriptExportSink(str(path), "vtt", clock=lambda: clock_val[0])
    sink.start()  # _prev_end_ms = 0
    clock_val[0] = 3.5
    sink.on_final(Transcript(text="vtt fallback", is_final=True))
    sink.stop()

    content = path.read_text(encoding="utf-8")
    assert "vtt fallback" in content
    # start_ms = _prev_end_ms (0); end_ms = elapsed (3500 ms)
    assert "00:00:00.000 --> 00:00:03.500" in content


# ---------------------------------------------------------------------------
# Lifecycle and flush
# ---------------------------------------------------------------------------


def test_sink_file_flushed_on_each_final(tmp_path):
    path = tmp_path / "out.txt"
    flush_calls: list[int] = []

    sink = TranscriptExportSink(str(path), "txt", clock=lambda: 0.0)
    sink.start()

    original_flush = sink._file.flush  # type: ignore[union-attr]

    def tracking_flush() -> None:
        flush_calls.append(1)
        original_flush()

    sink._file.flush = tracking_flush  # type: ignore[union-attr]
    sink.on_final(Transcript(text="a", is_final=True))
    sink.on_final(Transcript(text="b", is_final=True))
    sink.stop()

    # 2 explicit on_final flushes; close() may add one more — assert at least 2
    assert len(flush_calls) >= 2


def test_sink_stop_closes_file(tmp_path):
    path = tmp_path / "out.txt"
    sink = TranscriptExportSink(str(path), "txt", clock=lambda: 0.0)
    sink.start()
    assert sink._file is not None
    sink.stop()
    assert sink._file is None


def test_sink_on_final_before_start_is_noop(tmp_path):
    path = tmp_path / "out.txt"
    sink = TranscriptExportSink(str(path), "txt", clock=lambda: 0.0)
    # Should not raise even without start()
    sink.on_final(Transcript(text="ignored", is_final=True))
    assert not path.exists()


# ---------------------------------------------------------------------------
# Finding 3 fix: unsupported format raises ValueError at construction
# ---------------------------------------------------------------------------


def test_sink_invalid_format_raises():
    """Constructor with unsupported format must raise ValueError immediately,
    not silently fall through to VTT output."""
    import pytest

    with pytest.raises(ValueError, match="Unsupported format"):
        TranscriptExportSink("/dev/null", "ass")  # type: ignore[arg-type]


def test_sink_invalid_format_csv_raises():
    """'csv' is also unsupported and must raise."""
    import pytest

    with pytest.raises(ValueError, match="Unsupported format"):
        TranscriptExportSink("/dev/null", "csv")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Defect 1 fix: negative start_ms clamped to 0; cross-cue overlap prevented
# ---------------------------------------------------------------------------


def test_sink_negative_start_ms_clamped_to_zero_srt(tmp_path):
    """A transcript with start_ms < 0 must produce a cue starting at 00:00:00,000."""
    path = tmp_path / "out.srt"
    sink = TranscriptExportSink(str(path), "srt", clock=lambda: 0.0)
    sink.start()
    sink.on_final(Transcript(text="early", is_final=True, start_ms=-1, end_ms=500))
    sink.stop()

    content = path.read_text(encoding="utf-8")
    # start_ms=-1 must be clamped to 0
    assert "00:00:00,000 --> 00:00:00,500" in content


def test_sink_negative_start_ms_clamped_to_zero_vtt(tmp_path):
    """Same clamp for VTT format: negative start_ms yields 00:00:00.000."""
    path = tmp_path / "out.vtt"
    sink = TranscriptExportSink(str(path), "vtt", clock=lambda: 0.0)
    sink.start()
    sink.on_final(Transcript(text="early", is_final=True, start_ms=-500, end_ms=200))
    sink.stop()

    content = path.read_text(encoding="utf-8")
    # start_ms=-500 clamped to 0; end_ms=max(200,0)=200
    assert "00:00:00.000 --> 00:00:00.200" in content


def test_sink_overlapping_cue_start_pushed_to_prev_end(tmp_path):
    """When a cue's start_ms is before the previous cue's end_ms, it must be
    advanced to prev_end_ms so cues are monotonic and non-overlapping."""
    path = tmp_path / "out.srt"
    sink = TranscriptExportSink(str(path), "srt", clock=lambda: 0.0)
    sink.start()
    # First cue: 0 → 3000
    sink.on_final(Transcript(text="first", is_final=True, start_ms=0, end_ms=3000))
    # Second cue: backend supplies start_ms=1000, which overlaps with first cue's end (3000)
    sink.on_final(Transcript(text="second", is_final=True, start_ms=1000, end_ms=4000))
    sink.stop()

    content = path.read_text(encoding="utf-8")
    # Second cue start must be pushed to 3000 (prev end), not left at 1000
    assert "00:00:03,000 --> 00:00:04,000" in content
    # First cue must be untouched
    assert "00:00:00,000 --> 00:00:03,000" in content


def test_sink_cues_remain_monotonic_across_three_cues(tmp_path):
    """Three cues where the third has start_ms < second cue end_ms — all three
    must be monotonically non-decreasing with no overlap."""
    path = tmp_path / "out.srt"
    sink = TranscriptExportSink(str(path), "srt", clock=lambda: 0.0)
    sink.start()
    sink.on_final(Transcript(text="a", is_final=True, start_ms=0, end_ms=1000))
    sink.on_final(Transcript(text="b", is_final=True, start_ms=500, end_ms=2000))
    sink.on_final(Transcript(text="c", is_final=True, start_ms=500, end_ms=1500))
    sink.stop()

    content = path.read_text(encoding="utf-8")
    # cue a: 0→1000
    assert "00:00:00,000 --> 00:00:01,000" in content
    # cue b: start pushed to 1000 (prev end), end=max(2000,1000)=2000
    assert "00:00:01,000 --> 00:00:02,000" in content
    # cue c: start pushed to 2000 (prev end), end=max(1500,2000)=2000
    assert "00:00:02,000 --> 00:00:02,000" in content
