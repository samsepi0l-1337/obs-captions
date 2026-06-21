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


def test_local_agreement_returns_common_prefix_for_two_hypotheses():
    assert local_agreement(["안녕", "세상"], ["안녕", "여러분"], n=2) == ["안녕"]
    assert local_agreement(["a", "b", "c"], ["a", "b", "d"], n=2) == ["a", "b"]
    assert local_agreement(["x"], ["y"], n=2) == []


def test_local_agreement_n_one_confirms_current_tokens():
    assert local_agreement(["이전"], ["현재", "전체"], n=1) == ["현재", "전체"]
