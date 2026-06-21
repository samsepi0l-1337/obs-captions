"""M5 Benchmark: Path A (Browser Source WS) vs Path B (obs-websocket Text) local pipeline.

Usage:
    uv run python scripts/benchmark.py [--n 200]

Path A measures:
  - CaptionState.on_change → Hub.broadcast → WS client receive latency (p50/p95/max ms)
  - Server-process CPU% and RSS during a burst of rapid partial updates

Path B measures:
  - CaptionState.on_change → ObsTextSink._schedule_update → SetInputSettings call latency
    (includes the ~120 ms debounce window; p50/p95 ms)
  - NOTE: This is the LOCAL pipeline portion only.
    Real obs-websocket adds a network round-trip (~100 ms LAN, up to ~2 s under load).
    Measuring that round-trip requires a live OBS instance.

Requires: psutil (install via: uv sync --extra bench, or pip install psutil)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# psutil guard
# ---------------------------------------------------------------------------
try:
    import psutil  # type: ignore[import-untyped]

    _PSUTIL_OK = True
except ImportError:
    _PSUTIL_OK = False


# ---------------------------------------------------------------------------
# Minimal stubs so we never need a real server to be pre-running
# ---------------------------------------------------------------------------


def _src_on_path() -> None:
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    if src not in sys.path:
        sys.path.insert(0, src)


_src_on_path()

from obs_captions.config import AppConfig, ObsConfig  # noqa: E402
from obs_captions.obs_sink import ObsTextSink  # noqa: E402
from obs_captions.pipeline import CaptionState  # noqa: E402
from obs_captions.server.hub import Hub  # noqa: E402


# ---------------------------------------------------------------------------
# Fake WS client for Path B
# ---------------------------------------------------------------------------


@dataclass
class _FakeObs:
    """Records (emit_ts, call_ts) pairs for every SetInputSettings call."""

    calls: list[tuple[float, float]] = field(default_factory=list)
    _pending_emit_ts: float = field(default=0.0, init=False)
    url: str = ""
    password: str = ""

    async def connect(self) -> bool:  # noqa: D401
        return True

    async def wait_until_identified(self, timeout: float = 10) -> bool:
        return True

    async def call(self, request: Any) -> "_FakeResp":
        call_ts = time.perf_counter()
        if request.requestType == "SetInputSettings":
            self.calls.append((self._pending_emit_ts, call_ts))
        return _FakeResp(request.requestType)

    async def disconnect(self) -> None:
        pass


@dataclass
class _FakeResp:
    _req_type: str

    def ok(self) -> bool:
        return True

    @property
    def responseData(self) -> dict[str, Any]:
        if self._req_type == "GetInputList":
            return {"inputs": [{"inputName": "LiveCaptions", "inputKind": "text_ft2_source_v2"}]}
        return {}


# ---------------------------------------------------------------------------
# Path A benchmark
# ---------------------------------------------------------------------------


async def _bench_path_a(n: int) -> dict[str, Any]:
    """Emit N caption updates; measure emit→WS-receive latency + CPU/RSS."""
    import websockets

    from obs_captions.server.app import create_app, wire_caption_state

    hub = Hub()
    state = CaptionState()
    app = create_app(hub, overlay_dir=None)

    # Start server on a random free port
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)

    # We need to know the assigned port — patch serve to get it
    port_holder: list[int] = []
    original_startup = server.startup

    async def patched_startup(sockets: Any = None) -> None:
        await original_startup(sockets)
        for sock in server.servers[0].sockets:  # type: ignore[index]
            port_holder.append(sock.getsockname()[1])
            break

    server.startup = patched_startup  # type: ignore[method-assign]

    server_task = asyncio.create_task(server.serve())
    # Wait for startup
    for _ in range(50):
        if port_holder:
            break
        await asyncio.sleep(0.05)

    if not port_holder:
        server_task.cancel()
        raise RuntimeError("Server did not start in time")

    port = port_holder[0]
    wire_caption_state(state, hub, loop=asyncio.get_running_loop())

    latencies_ms: list[float] = []
    proc = psutil.Process(os.getpid()) if _PSUTIL_OK else None
    cpu_samples: list[float] = []
    rss_samples: list[int] = []

    uri = f"ws://127.0.0.1:{port}/ws"

    async with websockets.connect(uri) as ws:  # type: ignore[attr-defined]
        # Drain the initial state message
        await asyncio.wait_for(ws.recv(), timeout=2.0)

        if proc:
            proc.cpu_percent(interval=None)  # prime

        for i in range(n):
            emit_ts = time.perf_counter()
            # Alternate partials and finals to stress both code paths
            if i % 10 == 9:
                state.on_final(type("T", (), {"text": f"line {i // 10}", "is_final": True})())
            else:
                state.on_partial(type("T", (), {"text": f"partial {i}", "is_final": False})())

            try:
                await asyncio.wait_for(ws.recv(), timeout=1.0)
                recv_ts = time.perf_counter()
                latencies_ms.append((recv_ts - emit_ts) * 1000)
            except asyncio.TimeoutError:
                pass  # skip; hub may have deduped

            if proc and i % 20 == 0:
                cpu_samples.append(proc.cpu_percent(interval=None))
                rss_samples.append(proc.memory_info().rss)

            await asyncio.sleep(0.001)  # 1 ms between emissions (realistic partial rate)

    server.should_exit = True
    server_task.cancel()
    try:
        await server_task
    except (asyncio.CancelledError, Exception):
        pass

    result: dict[str, Any] = {
        "n_measured": len(latencies_ms),
    }
    if latencies_ms:
        s = sorted(latencies_ms)
        result["p50_ms"] = round(statistics.median(s), 2)
        result["p95_ms"] = round(s[int(len(s) * 0.95)], 2)
        result["max_ms"] = round(max(s), 2)
        result["min_ms"] = round(min(s), 2)
    if cpu_samples:
        result["cpu_pct_mean"] = round(statistics.mean(cpu_samples), 1)
        result["cpu_pct_max"] = round(max(cpu_samples), 1)
    if rss_samples:
        result["rss_mb_mean"] = round(statistics.mean(rss_samples) / 1024 / 1024, 1)
        result["rss_mb_max"] = round(max(rss_samples) / 1024 / 1024, 1)

    return result


# ---------------------------------------------------------------------------
# Path B benchmark
# ---------------------------------------------------------------------------


async def _bench_path_b(n: int, debounce_ms: int = 120) -> dict[str, Any]:
    """Emit N caption changes; measure state-change→SetInputSettings latency.

    NOTE: This measures the LOCAL pipeline only (CaptionState → debounce → mock call).
    The real obs-websocket round-trip (network + OBS processing) is NOT included.
    A live OBS instance is required to measure that portion.
    """
    obs_fake = _FakeObs()
    obs_config = ObsConfig(host="localhost", port=4455, source_name="LiveCaptions")
    app_config = AppConfig(obs=obs_config)
    state = CaptionState()

    sink = ObsTextSink(
        state=state,
        config=app_config,
        client=obs_fake,  # type: ignore[arg-type]
        debounce_ms=debounce_ms,
    )
    await sink.start()

    # Emit in bursts: each burst of 5 rapid partials + 1 final, then wait for debounce to fire.
    # This gives one SetInputSettings call per burst, yielding n // 6 measurable samples.
    burst_size = 5  # partials per burst before a final
    group_size = burst_size + 1  # 5 partials + 1 final
    n_groups = max(n // group_size, 10)

    for g in range(n_groups):
        # Rapid burst: emit_ts is the FIRST emission in the burst (worst-case latency)
        burst_start_ts = time.perf_counter()
        for j in range(burst_size):
            obs_fake._pending_emit_ts = burst_start_ts
            state.on_partial(type("T", (), {"text": f"partial g{g} j{j}", "is_final": False})())
            await asyncio.sleep(0.002)  # 2 ms between partials within a burst
        # Final emission closes the burst
        obs_fake._pending_emit_ts = burst_start_ts
        state.on_final(type("T", (), {"text": f"line {g}", "is_final": True})())
        # Wait for debounce + margin before next burst
        await asyncio.sleep(debounce_ms / 1000.0 + 0.08)

    # Small extra wait in case last debounce is still pending
    await asyncio.sleep(debounce_ms / 1000.0 + 0.05)
    await sink.stop()

    latencies_ms: list[float] = []
    for emit_ts, call_ts in obs_fake.calls:
        if emit_ts > 0:
            latencies_ms.append((call_ts - emit_ts) * 1000)

    result: dict[str, Any] = {
        "debounce_ms": debounce_ms,
        "n_emits": n_groups * group_size,
        "n_calls": len(obs_fake.calls),
        "note": (
            "LOCAL pipeline only. Real obs-websocket round-trip (~100 ms LAN "
            "to ~2 s under load) not included — requires live OBS."
        ),
    }
    if latencies_ms:
        s = sorted(latencies_ms)
        result["p50_ms"] = round(statistics.median(s), 2)
        result["p95_ms"] = round(s[int(len(s) * 0.95)], 2)
        result["max_ms"] = round(max(s), 2)

    return result


# ---------------------------------------------------------------------------
# Markdown table printer
# ---------------------------------------------------------------------------


def _print_results(path_a: dict[str, Any], path_b: dict[str, Any]) -> None:
    print()
    print("## Benchmark Results — OBS Captions Path A vs Path B")
    print()
    print("### Path A: Browser Source (WebSocket overlay)")
    print()
    print("| Metric | Value |")
    print("|--------|-------|")
    print(f"| Samples measured | {path_a.get('n_measured', 'N/A')} |")
    print(f"| Latency p50 | {path_a.get('p50_ms', 'N/A')} ms |")
    print(f"| Latency p95 | {path_a.get('p95_ms', 'N/A')} ms |")
    print(f"| Latency max | {path_a.get('max_ms', 'N/A')} ms |")
    print(f"| Latency min | {path_a.get('min_ms', 'N/A')} ms |")
    if "cpu_pct_mean" in path_a:
        print(f"| CPU% mean (burst) | {path_a['cpu_pct_mean']}% |")
        print(f"| CPU% max (burst) | {path_a['cpu_pct_max']}% |")
    else:
        print("| CPU% (burst) | N/A (psutil not installed) |")
    if "rss_mb_mean" in path_a:
        print(f"| RSS mean | {path_a['rss_mb_mean']} MB |")
        print(f"| RSS max | {path_a['rss_mb_max']} MB |")
    else:
        print("| RSS | N/A (psutil not installed) |")
    print()
    print("> Path A latency = emit_ts (state.on_partial/on_final called) → ws.recv() in client.")
    print()

    print("### Path B: obs-websocket Text source (local pipeline portion only)")
    print()
    print("| Metric | Value |")
    print("|--------|-------|")
    print(f"| Debounce window | {path_b.get('debounce_ms', 'N/A')} ms |")
    print(f"| Captions emitted | {path_b.get('n_emits', 'N/A')} |")
    print(f"| SetInputSettings calls | {path_b.get('n_calls', 'N/A')} |")
    print(f"| Local-pipeline p50 | {path_b.get('p50_ms', 'N/A')} ms |")
    print(f"| Local-pipeline p95 | {path_b.get('p95_ms', 'N/A')} ms |")
    print(f"| Local-pipeline max | {path_b.get('max_ms', 'N/A')} ms |")
    print()
    print(
        "> **NOTE**: Path B measures CaptionState-change → SetInputSettings mock-call latency,\n"
        "> which includes the debounce wait. The real obs-websocket round-trip\n"
        "> (network + OBS processing: ~100 ms on LAN, up to ~2 s under load)\n"
        "> is NOT included — it requires a live OBS instance to measure.\n"
        "> Total Path B end-to-end ≈ local-pipeline + obs-websocket round-trip."
    )
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument(
        "--n", type=int, default=200, help="Number of caption updates to emit (default: 200)"
    )
    p.add_argument(
        "--debounce-ms", type=int, default=120, help="Path B debounce window in ms (default: 120)"
    )
    return p.parse_args()


async def _main(n: int, debounce_ms: int) -> tuple[dict[str, Any], dict[str, Any]]:
    print(f"Running Path A benchmark (n={n})...")
    path_a = await _bench_path_a(n)
    print(f"  done. n_measured={path_a.get('n_measured')}")

    print(f"Running Path B benchmark (n={n}, debounce={debounce_ms} ms)...")
    path_b = await _bench_path_b(n, debounce_ms=debounce_ms)
    print(f"  done. n_calls={path_b.get('n_calls')}")

    return path_a, path_b


def main() -> None:
    if not _PSUTIL_OK:
        print(
            "WARNING: psutil not installed. CPU/RSS metrics will be skipped.\n"
            "Install via: uv sync --extra bench  or  pip install psutil\n"
        )
    args = _parse_args()
    path_a, path_b = asyncio.run(_main(args.n, args.debounce_ms))
    _print_results(path_a, path_b)


if __name__ == "__main__":
    main()
