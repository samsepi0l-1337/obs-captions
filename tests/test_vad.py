from __future__ import annotations

import numpy as np

from obs_captions.audio.capture import float32_to_pcm16
from obs_captions.vad import SileroVad, UtteranceSegmenter, VadEvent


class FakeModel:
    def __init__(self, probabilities: list[float]):
        self.probabilities = probabilities
        self.calls = 0

    def __call__(self, frame, sample_rate):
        assert sample_rate == 16000
        assert frame.dtype == np.float32
        value = self.probabilities[self.calls]
        self.calls += 1
        return value


def frame(value: float = 0.2) -> bytes:
    return float32_to_pcm16(np.full(1600, value, dtype=np.float32))


def test_silero_vad_is_speech_uses_stubbed_model_threshold():
    vad = SileroVad(model=FakeModel([0.2, 0.8]), threshold=0.5, window_size=1600)

    assert vad.is_speech(frame()) is False
    assert vad.is_speech(frame()) is True


def test_utterance_segmenter_yields_boundary_after_min_silence():
    vad = SileroVad(model=FakeModel([0.9, 0.8, 0.1, 0.1, 0.9]), threshold=0.5, window_size=1600)
    segmenter = UtteranceSegmenter(vad=vad, frame_ms=100, min_silence_ms=200)

    events = [segmenter.process(frame()) for _ in range(5)]

    assert events == [
        VadEvent(is_speech=True, segment=None),
        VadEvent(is_speech=True, segment=None),
        VadEvent(is_speech=False, segment=None),
        VadEvent(is_speech=False, segment=(0, 200)),
        VadEvent(is_speech=True, segment=None),
    ]


class TorchLikeModel:
    def __call__(self, frame, sample_rate):
        if not hasattr(frame, "dim"):
            raise AttributeError("'numpy.ndarray' object has no attribute 'dim'")
        return 0.7


def test_silero_vad_falls_back_to_torch_tensor_for_real_onnx_wrapper_shape():
    import pytest

    torch = pytest.importorskip("torch")
    # Skip if torch is incomplete (missing from_numpy)
    if not hasattr(torch, "from_numpy"):
        pytest.skip("torch.from_numpy not available")
    vad = SileroVad(model=TorchLikeModel(), threshold=0.5, window_size=1600)

    assert vad.is_speech(frame()) is True


def test_utterance_segmenter_flush_returns_pending_speech_segment():
    vad = SileroVad(model=FakeModel([0.9, 0.9]), threshold=0.5, window_size=1600)
    segmenter = UtteranceSegmenter(vad=vad, frame_ms=100, min_silence_ms=300)

    assert segmenter.process(frame()) == VadEvent(is_speech=True, segment=None)
    assert segmenter.process(frame()) == VadEvent(is_speech=True, segment=None)

    assert segmenter.flush() == (0, 200)
    assert segmenter.flush() is None


def test_utterance_segmenter_rejects_wrong_frame_length():
    vad = SileroVad(model=FakeModel([0.9]), threshold=0.5, window_size=1600)
    segmenter = UtteranceSegmenter(vad=vad, frame_ms=100, min_silence_ms=300)

    import pytest

    with pytest.raises(ValueError, match="expected .* bytes"):
        segmenter.process(b"\\x00\\x00" * 800)
