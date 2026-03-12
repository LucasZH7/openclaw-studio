#!/usr/bin/env python3
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "app_watchdog_state.json"
LOG_PATH = ROOT / "app_watchdog.log"
CRASH_DIR = Path.home() / "Library" / "Logs" / "DiagnosticReports"

APP_PATTERN = r"/Applications/OpenClaw.app/Contents/MacOS/OpenClaw"
APP_NAME = "OpenClaw"
GATEWAY_PATTERN = r"openclaw-gateway"

APP_RESTART_COOLDOWN_SEC = 45
GATEWAY_RESTART_COOLDOWN_SEC = 25
CRASH_WINDOW_SEC = 20 * 60
CRASH_LOOP_LIMIT = 4
CRASH_BACKOFF_SEC = 10 * 60


def _now() -> float:
    return time.time()


def _ts() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _log(msg: str) -> None:
    line = f"[{_ts()}] {msg}\n"
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line)


def _run(cmd: List[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
    )


def _is_running(pattern: str) -> bool:
    p = _run(["pgrep", "-f", pattern], timeout=8)
    return p.returncode == 0


def _load_state() -> Dict[str, object]:
    if not STATE_PATH.exists():
        return {
            "last_app_restart_ts": 0.0,
            "last_gateway_restart_ts": 0.0,
            "app_restart_events": [],
            "last_seen_crash_file": "",
            "backoff_until_ts": 0.0,
        }
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return {
        "last_app_restart_ts": 0.0,
        "last_gateway_restart_ts": 0.0,
        "app_restart_events": [],
        "last_seen_crash_file": "",
        "backoff_until_ts": 0.0,
    }


def _save_state(state: Dict[str, object]) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _latest_openclaw_crash() -> str:
    if not CRASH_DIR.exists():
        return ""
    files = sorted(CRASH_DIR.glob("OpenClaw-*.ips"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(files[0]) if files else ""


def _trim_events(events: List[float], now: float) -> List[float]:
    return [e for e in events if (now - e) <= CRASH_WINDOW_SEC]


def _restart_gateway(state: Dict[str, object], now: float) -> None:
    last_ts = float(state.get("last_gateway_restart_ts", 0.0) or 0.0)
    if (now - last_ts) < GATEWAY_RESTART_COOLDOWN_SEC:
        return
    uid = str(os.getuid())
    p = _run(["launchctl", "kickstart", "-k", f"gui/{uid}/ai.openclaw.gateway"], timeout=15)
    if p.returncode == 0:
        state["last_gateway_restart_ts"] = now
        _log("WATCHDOG: gateway down -> kickstart ai.openclaw.gateway")
    else:
        _log(f"WATCHDOG: gateway kickstart failed: {(p.stdout or '').strip()[:200]}")


def _restart_app(state: Dict[str, object], now: float, reason: str) -> None:
    backoff_until = float(state.get("backoff_until_ts", 0.0) or 0.0)
    if now < backoff_until:
        return

    last_ts = float(state.get("last_app_restart_ts", 0.0) or 0.0)
    if (now - last_ts) < APP_RESTART_COOLDOWN_SEC:
        return

    p = _run(["open", "-gj", "-a", APP_NAME], timeout=15)
    if p.returncode == 0:
        state["last_app_restart_ts"] = now
        events = list(state.get("app_restart_events", []))
        events = [float(x) for x in events if isinstance(x, (int, float))]
        events.append(now)
        events = _trim_events(events, now)
        state["app_restart_events"] = events
        if len(events) >= CRASH_LOOP_LIMIT:
            state["backoff_until_ts"] = now + CRASH_BACKOFF_SEC
            _log(
                "WATCHDOG: app crash loop detected; enter backoff "
                f"{int(CRASH_BACKOFF_SEC/60)}m to avoid restart storm"
            )
        _log(f"WATCHDOG: app not running ({reason}) -> launch OpenClaw")
    else:
        _log(f"WATCHDOG: app launch failed: {(p.stdout or '').strip()[:200]}")


def main() -> None:
    now = _now()
    state = _load_state()

    # 1) Gateway should stay alive regardless of app status.
    if not _is_running(GATEWAY_PATTERN):
        _restart_gateway(state, now)

    # 2) Detect app crash file progression and relaunch app if needed.
    latest_crash = _latest_openclaw_crash()
    last_seen = str(state.get("last_seen_crash_file", "") or "")
    if latest_crash and latest_crash != last_seen:
        state["last_seen_crash_file"] = latest_crash
        _log(f"WATCHDOG: observed app crash report -> {Path(latest_crash).name}")

    app_running = _is_running(APP_PATTERN)
    if not app_running:
        reason = "recent crash report" if latest_crash and latest_crash != last_seen else "process missing"
        _restart_app(state, now, reason)

    _save_state(state)


if __name__ == "__main__":
    main()
