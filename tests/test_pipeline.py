from obs_captions.pipeline import CaptionSnapshot, CaptionState, local_agreement
from obs_captions.stt import Transcript


def partial(text: str) -> Transcript:
    return Transcript(text=text, is_final=False)


def final(text: str) -> Transcript:
    return Transcript(text=text, is_final=True)


def test_partial_to_final_transition_clears_partial_and_commits_text():
    state = CaptionState(max_lines=3)

    state.on_partial(partial("안녕"))
    assert state.snapshot() == CaptionSnapshot(committed=[], partial="안녕")

    state.on_final(final("안녕하세요"))

    assert state.snapshot() == CaptionSnapshot(committed=["안녕하세요"], partial="")


def test_partial_updates_replace_tail_without_mutating_committed_lines():
    state = CaptionState(max_lines=3)
    state.on_final(final("첫 줄"))

    state.on_partial(partial("두"))
    state.on_partial(partial("두 번째 줄"))

    assert state.snapshot() == CaptionSnapshot(committed=["첫 줄"], partial="두 번째 줄")


def test_final_lines_trim_to_max_lines():
    state = CaptionState(max_lines=2)

    state.on_final(final("하나"))
    state.on_final(final("둘"))
    state.on_final(final("셋"))

    assert state.snapshot() == CaptionSnapshot(committed=["둘", "셋"], partial="")


def test_consecutive_duplicate_finals_are_deduped():
    state = CaptionState(max_lines=3)

    state.on_final(final("반복"))
    state.on_final(final("반복"))
    state.on_final(final("다음"))

    assert state.snapshot() == CaptionSnapshot(committed=["반복", "다음"], partial="")


def test_on_change_fires_only_when_snapshot_changes():
    snapshots: list[CaptionSnapshot] = []
    state = CaptionState(max_lines=3, on_change=snapshots.append)

    state.on_partial(partial("가"))
    state.on_partial(partial("가"))
    state.on_partial(partial("가나"))
    state.on_final(final("가나다"))
    state.on_final(final("가나다"))

    assert snapshots == [
        CaptionSnapshot(committed=[], partial="가"),
        CaptionSnapshot(committed=[], partial="가나"),
        CaptionSnapshot(committed=["가나다"], partial=""),
    ]


def test_subscribe_notifies_multiple_subscribers_and_unsubscribe_removes_one():
    a: list[CaptionSnapshot] = []
    b: list[CaptionSnapshot] = []
    state = CaptionState(max_lines=3)

    unsub_a = state.subscribe(a.append)
    state.subscribe(b.append)

    state.on_partial(partial("가"))
    assert a == [CaptionSnapshot(committed=[], partial="가")]
    assert b == [CaptionSnapshot(committed=[], partial="가")]

    unsub_a()
    state.on_partial(partial("가나"))

    assert a == [CaptionSnapshot(committed=[], partial="가")]
    assert b == [
        CaptionSnapshot(committed=[], partial="가"),
        CaptionSnapshot(committed=[], partial="가나"),
    ]


def test_on_change_param_coexists_with_subscribe():
    via_param: list[CaptionSnapshot] = []
    via_subscribe: list[CaptionSnapshot] = []
    state = CaptionState(max_lines=3, on_change=via_param.append)
    state.subscribe(via_subscribe.append)

    state.on_partial(partial("둘"))

    assert via_param == [CaptionSnapshot(committed=[], partial="둘")]
    assert via_subscribe == [CaptionSnapshot(committed=[], partial="둘")]


def test_local_agreement_returns_common_prefix_for_two_hypotheses():
    assert local_agreement(["안녕", "세상"], ["안녕", "여러분"], n=2) == ["안녕"]
    assert local_agreement(["a", "b", "c"], ["a", "b", "d"], n=2) == ["a", "b"]
    assert local_agreement(["x"], ["y"], n=2) == []


def test_local_agreement_n_one_confirms_current_tokens():
    assert local_agreement(["이전"], ["현재", "전체"], n=1) == ["현재", "전체"]


# ---------------------------------------------------------------------------
# CaptionState.clear()
# ---------------------------------------------------------------------------


def test_caption_state_clear_empties_committed_and_partial():
    state = CaptionState(max_lines=3)
    state.on_final(final("첫 줄"))
    state.on_partial(partial("두 번째 줄"))
    assert state.snapshot() == CaptionSnapshot(committed=["첫 줄"], partial="두 번째 줄")

    state.clear()

    assert state.snapshot() == CaptionSnapshot(committed=[], partial="")


def test_caption_state_clear_notifies_subscribers():
    snapshots: list[CaptionSnapshot] = []
    state = CaptionState(max_lines=3, on_change=snapshots.append)
    state.on_final(final("안녕"))

    state.clear()

    assert snapshots[-1] == CaptionSnapshot(committed=[], partial="")


def test_caption_state_clear_on_already_empty_does_not_notify():
    """clear() on an already-empty state must not fire subscribers (no change)."""
    snapshots: list[CaptionSnapshot] = []
    state = CaptionState(max_lines=3, on_change=snapshots.append)

    state.clear()

    assert snapshots == []


# ---------------------------------------------------------------------------
# CaptionState.__init__ validation (line 26) and subscribe/unsubscribe (lines 43-44)
# ---------------------------------------------------------------------------


def test_caption_state_raises_on_negative_max_lines() -> None:
    """max_lines < 0 raises ValueError (line 26 coverage)."""
    import pytest as _pytest

    with _pytest.raises(ValueError, match="max_lines must be >= 0"):
        CaptionState(max_lines=-1)


def test_unsubscribe_removes_callback_from_subscribers() -> None:
    """Calling the returned unsubscribe fn removes the subscriber (lines 43-44 coverage)."""
    received: list[CaptionSnapshot] = []
    state = CaptionState(max_lines=3)
    unsub = state.subscribe(received.append)

    state.on_partial(partial("first"))
    assert len(received) == 1

    unsub()  # executes lines 43-44: try + self._subscribers.remove(callback)

    state.on_partial(partial("second"))
    assert len(received) == 1  # subscriber was removed; second notification not received


def test_unsubscribe_called_twice_does_not_raise() -> None:
    """Calling unsubscribe twice triggers except ValueError: pass (lines 45-46 coverage)."""
    state = CaptionState(max_lines=3)
    unsub = state.subscribe(lambda s: None)
    unsub()  # first call: successful remove (lines 43-44)
    unsub()  # second call: ValueError caught by except block (lines 45-46)
