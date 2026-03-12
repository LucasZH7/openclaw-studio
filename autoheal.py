#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from monitor_config import load_monitor_config
from server import OpenClawAdapter, collect_status  # type: ignore

ROOT = Path(__file__).resolve().parent
HEAL_LOG = ROOT / "heal.log"
HEAL_STATE = ROOT / "heal_state.json"
AUTOHEAL_PID = ROOT / "autoheal.pid"

INTERVAL_SEC = 30


def load_config() -> dict:
    return load_monitor_config()


def log(msg: str) -> None:
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    line = f"[{ts}] {msg}\n"
    HEAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with HEAL_LOG.open("a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="")


def load_state() -> dict:
    if not HEAL_STATE.exists():
        return {
            "consecutive_stalled": 0,
            "consecutive_overdue": 0,
            "last_soft_heal_ts": 0,
            "last_hard_heal_ts": 0,
            "last_action": "none",
        }
    try:
        return json.loads(HEAL_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {
            "consecutive_stalled": 0,
            "consecutive_overdue": 0,
            "last_soft_heal_ts": 0,
            "last_hard_heal_ts": 0,
            "last_action": "state_reset",
        }


def save_state(state: dict) -> None:
    HEAL_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def run_cmd(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        env = os.environ.copy()
        if cmd:
            cli_dir = str(Path(cmd[0]).parent)
            path = env.get("PATH", "")
            env["PATH"] = f"{cli_dir}:{path}" if path else cli_dir
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            env=env,
        )
        return p.returncode, (p.stdout or "").strip()
    except Exception as e:
        return 1, str(e)


def resolve_openclaw_cli() -> Optional[str]:
    env_cli = os.getenv("OPENCLAW_CLI")
    if env_cli and Path(env_cli).exists():
        return env_cli
    which_cli = shutil.which("openclaw")
    if which_cli:
        return which_cli
    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    if nvm_root.exists():
        candidates = sorted(nvm_root.glob("*/bin/openclaw"), reverse=True)
        for c in candidates:
            if c.exists():
                return str(c)
    return None


def get_main_session_id() -> Optional[str]:
    adapter = OpenClawAdapter(load_monitor_config())
    return adapter._main_session_id()


def soft_heal(reason: str, cfg: dict) -> None:
    monitoring = cfg.get("monitoring", {})
    cli = resolve_openclaw_cli()
    if not cli:
        log(f"SOFT_HEAL failed ({reason}): 未找到 openclaw 命令")
        return

    sid = get_main_session_id()
    must_reply = bool(monitoring.get("require_reply_after_nudge", True))
    reply_rule = "你必须先回复“收到，正在工作”，然后再汇报动作。" if must_reply else "请立即开始执行。"
    message = (
        "自动修复触发（无需用户催）："
        f"{reason}。"
        f"{reply_rule}"
        "请立刻执行当前case的一个可验证动作，并按格式回报：当前动作/产物路径/下一步。"
        "禁止只回复承诺句。"
    )
    cmd = [cli, "agent"]
    if sid:
        cmd += ["--session-id", sid]
    cmd += ["--message", message, "--json", "--deliver"]
    code, out = run_cmd(cmd, timeout=180)
    if code == 0:
        log(f"SOFT_HEAL ok: {reason}")
    else:
        log(f"SOFT_HEAL failed ({reason}): {out[:300]}")


def hard_heal(reason: str) -> None:
    cli = resolve_openclaw_cli()
    if not cli:
        log(f"HARD_HEAL failed ({reason}): 未找到 openclaw 命令")
        return
    code, out = run_cmd([cli, "gateway", "restart"], timeout=120)
    if code == 0:
        log(f"HARD_HEAL ok: {reason}")
    else:
        log(f"HARD_HEAL failed ({reason}): {out[:300]}")


def tick(state: dict) -> dict:
    cfg = load_config()
    monitoring = cfg.get("monitoring", {})
    STALL_THRESHOLD = int(monitoring.get("normal_idle_threshold_rounds", 4))
    OVERDUE_THRESHOLD = int(monitoring.get("overdue_threshold_rounds", 2))
    SOFT_COOLDOWN_SEC = int(monitoring.get("soft_heal_cooldown_sec", 180))
    HARD_COOLDOWN_SEC = int(monitoring.get("hard_heal_cooldown_sec", 420))
    BOSS_FORCE = bool(monitoring.get("force_work_mode", True))
    ALLOW_HARD_RESTART = bool(monitoring.get("allow_hard_restart", False))

    now = time.time()
    s = collect_status()
    compat = s.get("compat", {})
    st = compat.get("state")
    should_work = bool(compat.get("should_work"))

    if st == "stalled":
        state["consecutive_stalled"] = int(state.get("consecutive_stalled", 0)) + 1
    elif st == "down":
        state["consecutive_stalled"] = STALL_THRESHOLD + 2
    else:
        state["consecutive_stalled"] = 0

    if st == "overdue":
        state["consecutive_overdue"] = int(state.get("consecutive_overdue", 0)) + 1
    else:
        state["consecutive_overdue"] = 0

    # should_work + idle/thinking 也算轻度偷懒信号
    if should_work and st in {"idle", "thinking"}:
        state["consecutive_overdue"] = int(state.get("consecutive_overdue", 0)) + 1

    # 老板强推：不管 waiting 标记，只要静默过久就算 overdue 信号
    session_age = compat.get("session_age_sec")
    force_idle_sec = int(monitoring.get("force_idle_threshold_sec", 180))
    if BOSS_FORCE and isinstance(session_age, (int, float)) and session_age > force_idle_sec and st in {"idle", "thinking", "overdue"}:
        state["consecutive_overdue"] = int(state.get("consecutive_overdue", 0)) + 1

    stalled_count = int(state.get("consecutive_stalled", 0))
    overdue_count = int(state.get("consecutive_overdue", 0))
    last_soft = float(state.get("last_soft_heal_ts", 0))
    last_hard = float(state.get("last_hard_heal_ts", 0))

    if (stalled_count >= STALL_THRESHOLD or overdue_count >= OVERDUE_THRESHOLD) and (now - last_soft) >= SOFT_COOLDOWN_SEC:
        reason = "长时间空转" if stalled_count >= STALL_THRESHOLD else "应工作但未活动"
        soft_heal(reason, cfg)
        state["last_soft_heal_ts"] = now
        state["last_action"] = f"soft_heal:{reason}"

    if ALLOW_HARD_RESTART and stalled_count >= STALL_THRESHOLD + 4 and (now - last_hard) >= HARD_COOLDOWN_SEC:
        reason = "soft heal 后仍空转"
        hard_heal(reason)
        state["last_hard_heal_ts"] = now
        state["last_action"] = f"hard_heal:{reason}"

    state["last_state"] = st
    state["last_should_work"] = should_work
    state["last_seen"] = datetime.now().astimezone().isoformat(timespec="seconds")
    save_state(state)
    return state


def main() -> None:
    AUTOHEAL_PID.write_text(str(os.getpid()), encoding="utf-8")
    log("AUTOHEAL started")
    state = load_state()
    save_state(state)
    while True:
        try:
            state = tick(state)
        except Exception as e:
            log(f"AUTOHEAL tick error: {e}")
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
