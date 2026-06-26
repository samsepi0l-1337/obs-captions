"""Backend smoke-test helpers for the `check-engine` CLI command.

Extracted from cli.py so that cli.py stays under 400 lines and these
helpers can be unit-tested and monkeypatched independently.
"""
from __future__ import annotations

import asyncio
from typing import Any

import click


async def _check_engine(
    engine: str,
    wav_path: str | None,
    seconds: int,
    language: str | None,
    config_path: str | None,
) -> int:
    """Return 0 on success, 1 on failure."""
    from obs_captions.config import load_config
    from obs_captions.stt.registry import create_backend

    config = load_config(config_path)
    # Override engine + language for this smoke run without mutating the model.
    object.__setattr__(config, "engine", engine)  # type: ignore[arg-type]
    if language is not None:
        object.__setattr__(config, "language", language)  # type: ignore[arg-type]

    finals: list[str] = []
    partials: list[str] = []

    def on_partial(t: Any) -> None:
        partials.append(t.text)
        click.echo(f"[partial] {t.text}")

    def on_final(t: Any) -> None:
        finals.append(t.text)
        click.echo(f"[final]   {t.text}")

    try:
        backend = create_backend(config, on_partial=on_partial, on_final=on_final)
    except ValueError as exc:
        click.echo(f"ERROR: {exc}", err=True)
        return 1

    await backend.start_stream()

    try:
        if wav_path is not None:
            await _stream_wav(backend, wav_path, seconds)
            await backend.flush()
            # Wait briefly for in-flight transcript events from streaming providers
            # (e.g. AssemblyAI/Deepgram) whose flush() is a no-op and whose recv
            # loop processes events asynchronously.  We poll up to the remaining
            # deadline for at least one final before stopping.
            deadline = asyncio.get_running_loop().time() + seconds
            while not finals and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
        else:
            click.echo(
                f"Connectivity check for engine '{engine}' — no WAV provided, stopping immediately."
            )
    finally:
        await backend.stop_stream()

    if wav_path is not None:
        n_finals = len(finals)
        n_partials = len(partials)
        if n_finals == 0:
            click.echo(
                "WARNING: 0 final(s) received after streaming WAV — "
                "provider may not have transcribed audio.",
                err=True,
            )
        click.echo(f"Done. {n_finals} final(s), {n_partials} partial(s) received.")
    else:
        click.echo(f"Engine '{engine}' started and stopped successfully.")
    return 0


async def _stream_wav(backend: Any, wav_path: str, max_seconds: int) -> None:
    """Read a WAV file and feed PCM16 chunks into the backend."""
    import wave

    chunk_samples = 1600  # 100 ms of 16 kHz mono
    deadline = asyncio.get_running_loop().time() + max_seconds

    with wave.open(wav_path, "rb") as wf:
        src_rate = wf.getframerate()
        src_channels = wf.getnchannels()
        src_width = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    # Normalize to 16-bit PCM
    if src_width != 2:
        import audioop  # noqa: PLC0415  # TODO: migrate to numpy when Python 3.13 is supported (audioop removed in 3.13)  # noqa: DEP001

        raw = audioop.lin2lin(raw, src_width, 2)

    # Mix down to mono if stereo
    if src_channels > 1:
        import audioop  # noqa: PLC0415  # TODO: migrate to numpy when Python 3.13 is supported (audioop removed in 3.13)  # noqa: DEP001

        raw = audioop.tomono(raw, 2, 0.5, 0.5)

    # Resample to 16000 Hz if needed
    if src_rate != 16000:
        import audioop  # noqa: PLC0415  # TODO: migrate to numpy when Python 3.13 is supported (audioop removed in 3.13)  # noqa: DEP001

        raw, _ = audioop.ratecv(raw, 2, 1, src_rate, 16000, None)

    # Feed in 100ms chunks
    offset = 0
    chunk_bytes = chunk_samples * 2
    while offset < len(raw):
        if asyncio.get_running_loop().time() >= deadline:
            click.echo(f"[check-engine] Reached {max_seconds}s limit.", err=True)
            break
        chunk = raw[offset : offset + chunk_bytes]
        await backend.feed_audio(chunk)
        offset += chunk_bytes
        await asyncio.sleep(0.1)
