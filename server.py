#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from monitor_config import load_monitor_config

ROOT = Path(__file__).resolve().parent
INDEX_HTML = ROOT / "index.html"
HEAL_STATE = ROOT / "heal_state.json"
AUTOHEAL_PID = ROOT / "autoheal.pid"
NUDGE_LOG = ROOT / "nudge.log"
METRICS_HISTORY = ROOT / "metrics_history.jsonl"

LAST_LOG_SIZE: Dict[str, Optional[int]] = {}
LAST_HISTORY_MINUTE: Optional[str] = None


def _check_process(pattern: str) -> bool:
    proc = subprocess.run(["pgrep", "-f", pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0


def _safe_stat_mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _clamp(v: float, lo: int = 0, hi: int = 100) -> int:
    return int(max(lo, min(hi, round(v))))


def _format_ts() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _grade_score(score: int) -> str:
    if score >= 85:
        return "excellent"
    if score >= 65:
        return "good"
    if score >= 40:
        return "fair"
    return "poor"


def _append_history_sample(payload: Dict[str, Any]) -> None:
    global LAST_HISTORY_MINUTE
    minute_key = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M")
    if LAST_HISTORY_MINUTE == minute_key:
        return
    LAST_HISTORY_MINUTE = minute_key

    metrics = payload.get("metrics", {})
    runtime = payload.get("runtime", {})
    compat = payload.get("compat", {})
    session = compat.get("session", {}) if isinstance(compat.get("session"), dict) else {}
    record = {
        "timestamp": payload.get("timestamp"),
        "score": metrics.get("score"),
        "grade": _grade_score(int(metrics.get("score", 0))),
        "state": payload.get("status", {}).get("code"),
        "agent_type": payload.get("agent", {}).get("type"),
        "session_age_sec": metrics.get("session_age_sec"),
        "input_tokens": session.get("input_tokens", 0),
        "output_tokens": session.get("output_tokens", 0),
        "total_tokens": session.get("total_tokens", 0),
        "context_tokens": session.get("context_tokens", 0),
        "gateway_running": runtime.get("gateway_running"),
    }
    with METRICS_HISTORY.open("a", encoding="utf-8") as handle:
      handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_history_records() -> List[Dict[str, Any]]:
    if not METRICS_HISTORY.exists():
        return []
    records: List[Dict[str, Any]] = []
    for line in METRICS_HISTORY.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _history_bucket_label(dt: datetime, range_name: str) -> str:
    if range_name == "day":
        return dt.strftime("%H:00")
    if range_name == "week":
        return dt.strftime("%a")
    if range_name == "month":
        return dt.strftime("%m-%d")
    return dt.strftime("%Y-%m")


def _history_cutoff(range_name: str, now: datetime) -> datetime:
    seconds = {
        "day": 86400,
        "week": 86400 * 7,
        "month": 86400 * 31,
        "year": 86400 * 366,
    }.get(range_name, 86400)
    return now.fromtimestamp(now.timestamp() - seconds, tz=now.tzinfo)


def build_history_payload(range_name: str = "day") -> Dict[str, Any]:
    now = datetime.now().astimezone()
    cutoff = _history_cutoff(range_name, now)
    buckets: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "label": "",
        "scores": [],
        "total_tokens": [],
        "input_tokens": [],
        "output_tokens": [],
        "states": defaultdict(int),
    })

    for item in _load_history_records():
        ts = item.get("timestamp")
        if not isinstance(ts, str):
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if dt < cutoff:
            continue
        label = _history_bucket_label(dt, range_name)
        bucket = buckets[label]
        bucket["label"] = label
        if isinstance(item.get("score"), int):
            bucket["scores"].append(item["score"])
        for key in ("total_tokens", "input_tokens", "output_tokens"):
            value = item.get(key)
            if isinstance(value, int):
                bucket[key].append(value)
        state = item.get("state")
        if isinstance(state, str):
            bucket["states"][state] += 1

    points = []
    average_scores: List[int] = []
    token_totals: List[int] = []
    for label in sorted(buckets.keys()):
        bucket = buckets[label]
        avg_score = round(sum(bucket["scores"]) / len(bucket["scores"])) if bucket["scores"] else 0
        avg_tokens = round(sum(bucket["total_tokens"]) / len(bucket["total_tokens"])) if bucket["total_tokens"] else 0
        top_state = max(bucket["states"].items(), key=lambda pair: pair[1])[0] if bucket["states"] else "unknown"
        points.append({
            "label": label,
            "avg_score": avg_score,
            "grade": _grade_score(avg_score),
            "avg_total_tokens": avg_tokens,
            "avg_input_tokens": round(sum(bucket["input_tokens"]) / len(bucket["input_tokens"])) if bucket["input_tokens"] else 0,
            "avg_output_tokens": round(sum(bucket["output_tokens"]) / len(bucket["output_tokens"])) if bucket["output_tokens"] else 0,
            "dominant_state": top_state,
        })
        average_scores.append(avg_score)
        token_totals.append(avg_tokens)

    overall_score = round(sum(average_scores) / len(average_scores)) if average_scores else 0
    return {
        "range": range_name,
        "points": points,
        "summary": {
            "avg_score": overall_score,
            "grade": _grade_score(overall_score),
            "avg_total_tokens": round(sum(token_totals) / len(token_totals)) if token_totals else 0,
            "sample_count": len(points),
        },
    }


def _history_sampler(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            collect_status()
        except Exception:
            pass
        stop_event.wait(60)


class OpenClawAdapter:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.agent_cfg = config.get("agent", {})
        self.oc_cfg = self.agent_cfg.get("openclaw", {})
        self.monitoring_cfg = config.get("monitoring", {})

        openclaw_home = Path(self.oc_cfg.get("home", str(Path.home() / ".openclaw"))).expanduser()
        self.openclaw_home = openclaw_home
        self.sessions_path = openclaw_home / "agents" / "main" / "sessions" / "sessions.json"
        self.gateway_log = openclaw_home / "logs" / "gateway.log"
        self.case_roots = [Path(p).expanduser() for p in self.oc_cfg.get("case_roots", [])]
        self.session_key = str(self.oc_cfg.get("session_key", "agent:main:main"))

    def _resolve_cli(self) -> Optional[str]:
        env_cli = os.getenv("OPENCLAW_CLI")
        if env_cli and Path(env_cli).exists():
            return env_cli

        which_cli = shutil.which("openclaw")
        if which_cli:
            return which_cli

        nvm_bin = Path.home() / ".nvm" / "versions" / "node"
        if nvm_bin.exists():
            candidates = sorted(nvm_bin.glob("*/bin/openclaw"), reverse=True)
            for candidate in candidates:
                if candidate.exists():
                    return str(candidate)
        return None

    def _load_sessions(self) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(self.sessions_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def _main_session(self) -> Optional[Dict[str, Any]]:
        data = self._load_sessions()
        if not data:
            return None
        session = data.get(self.session_key)
        return session if isinstance(session, dict) else None

    def _session_jsonl_path(self) -> Optional[Path]:
        session = self._main_session()
        if not session:
            return None
        sid = session.get("sessionId")
        if not isinstance(sid, str) or not sid:
            return None
        path = self.openclaw_home / "agents" / "main" / "sessions" / f"{sid}.jsonl"
        return path if path.exists() else None

    def _main_session_id(self) -> Optional[str]:
        session = self._main_session()
        if not session:
            return None
        sid = session.get("sessionId")
        return sid if isinstance(sid, str) and sid else None

    def _load_session_age_sec(self) -> Tuple[Optional[float], Optional[str]]:
        session = self._main_session()
        if not session:
            return None, None
        updated_ms = session.get("updatedAt")
        if not isinstance(updated_ms, int):
            return None, None
        age_sec = max(0.0, time.time() - (updated_ms / 1000.0))
        iso = datetime.fromtimestamp(updated_ms / 1000.0, tz=timezone.utc).astimezone().isoformat()
        return age_sec, iso

    def _latest_case_file(self) -> Tuple[Optional[Path], Optional[float]]:
        latest_path: Optional[Path] = None
        latest_mtime = 0.0
        for root in self.case_roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.name == ".DS_Store":
                    continue
                try:
                    mtime = path.stat().st_mtime
                except (FileNotFoundError, PermissionError, OSError):
                    continue
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest_path = path
        if latest_path is None:
            return None, None
        return latest_path, latest_mtime

    def _latest_task_file(self) -> Optional[Path]:
        best_path = None
        best_mtime = 0.0
        for root in self.case_roots:
            if not root.exists():
                continue
            for path in root.rglob("TASK.md"):
                try:
                    mtime = path.stat().st_mtime
                except (FileNotFoundError, PermissionError, OSError):
                    continue
                if mtime > best_mtime:
                    best_mtime = mtime
                    best_path = path
        return best_path

    def _extract_should_work(self, task_file: Optional[Path]) -> Tuple[bool, bool, str, str]:
        if task_file is None or not task_file.exists():
            return False, False, "task_missing", "TASK.md not found"

        try:
            text = task_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return False, False, "task_unreadable", "TASK.md cannot be read"

        status_match = re.search(r"^-\s*Status:\s*(.+)$", text, flags=re.MULTILINE | re.IGNORECASE)
        next_match = re.search(r"^-\s*Next Action:\s*(.+)$", text, flags=re.MULTILINE | re.IGNORECASE)

        status = status_match.group(1).strip().lower() if status_match else "unknown"
        next_action = next_match.group(1).strip().lower() if next_match else ""

        done_keywords = ["done", "completed", "closed", "archived", "finish"]
        wait_keywords = ["wait", "waiting", "等待", "等回复", "clarification", "hold", "pending reply"]

        if any(word in status for word in done_keywords):
            return False, False, "task_done", f"Status is {status}"

        if any(word in next_action for word in wait_keywords):
            return False, True, "waiting_external", "Next action indicates waiting for external reply"

        return True, False, "should_work", f"Status is {status}"

    def _log_delta_bytes(self) -> int:
        key = str(self.gateway_log)
        try:
            current = self.gateway_log.stat().st_size
        except (FileNotFoundError, PermissionError, OSError):
            return 0

        previous = LAST_LOG_SIZE.get(key)
        LAST_LOG_SIZE[key] = current
        if previous is None:
            return 0
        return max(0, current - previous)

    def collect_status(self) -> Dict[str, Any]:
        now = time.time()
        gateway_running = _check_process("openclaw-gateway")
        app_running = _check_process("OpenClaw.app/Contents/MacOS/OpenClaw")
        session = self._main_session() or {}

        session_age_sec, session_updated_iso = self._load_session_age_sec()
        case_path, case_mtime = self._latest_case_file()
        case_age_sec = None if case_mtime is None else max(0.0, now - case_mtime)

        task_file = self._latest_task_file()
        should_work, waiting_external, should_work_reason_key, should_work_reason = self._extract_should_work(task_file)

        log_mtime = _safe_stat_mtime(self.gateway_log)
        log_age_sec = None if log_mtime is None else max(0.0, now - log_mtime)
        log_delta = self._log_delta_bytes()

        force_work_mode = bool(self.monitoring_cfg.get("force_work_mode", True))
        force_idle_sec = int(self.monitoring_cfg.get("force_idle_threshold_sec", 180))

        if not gateway_running:
            state = "down"
            state_reason = "gateway_down"
        elif session_age_sec is not None and session_age_sec <= 90 and (
            (case_age_sec is not None and case_age_sec <= 240) or (log_age_sec is not None and log_age_sec <= 30)
        ):
            state = "working"
            state_reason = "recent_activity"
        elif should_work and session_age_sec is not None and session_age_sec > 240:
            state = "overdue"
            state_reason = "should_work_but_idle"
        elif force_work_mode and session_age_sec is not None and session_age_sec > force_idle_sec:
            state = "overdue"
            state_reason = "forced_idle_timeout"
        elif session_age_sec is not None and session_age_sec <= 240:
            state = "thinking"
            state_reason = "recent_session_without_output"
        elif session_age_sec is not None and session_age_sec > 600 and (case_age_sec is None or case_age_sec > 600):
            state = "stalled"
            state_reason = "long_idle"
        else:
            state = "idle"
            state_reason = "standby"

        focus = 100 if state == "working" else 55 if state == "thinking" else 20 if state in {"stalled", "overdue"} else 35
        throughput = _clamp((log_delta / 256.0) * 100)
        freshness_raw = 0 if session_age_sec is None else 100 - (session_age_sec / 6.0)
        freshness = _clamp(freshness_raw)
        score = _clamp((focus * 0.5) + (throughput * 0.2) + (freshness * 0.3))

        autoheal_running = False
        autoheal_pid = None
        if AUTOHEAL_PID.exists():
            try:
                autoheal_pid = int(AUTOHEAL_PID.read_text(encoding="utf-8").strip())
                proc = subprocess.run(["ps", "-p", str(autoheal_pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                autoheal_running = proc.returncode == 0
            except Exception:
                autoheal_running = False

        heal_state = None
        if HEAL_STATE.exists():
            try:
                heal_state = json.loads(HEAL_STATE.read_text(encoding="utf-8"))
            except Exception:
                heal_state = None

        return {
            "agent_id": self.agent_cfg.get("id", "main"),
            "agent_type": self.agent_cfg.get("type", "openclaw"),
            "agent_name": self.agent_cfg.get("robot_name") or self.agent_cfg.get("display_name", "OpenClaw Main"),
            "state": state,
            "state_reason": state_reason,
            "gateway_running": gateway_running,
            "app_running": app_running,
            "session_age_sec": None if session_age_sec is None else round(session_age_sec, 1),
            "session_updated_at": session_updated_iso,
            "case_age_sec": None if case_age_sec is None else round(case_age_sec, 1),
            "latest_case_file": str(case_path) if case_path else None,
            "latest_task_file": str(task_file) if task_file else None,
            "should_work": should_work,
            "waiting_external": waiting_external,
            "should_work_reason_key": should_work_reason_key,
            "should_work_reason": should_work_reason,
            "log_age_sec": None if log_age_sec is None else round(log_age_sec, 1),
            "log_delta_bytes": log_delta,
            "score": score,
            "meters": {
                "focus": focus,
                "throughput": throughput,
                "freshness": freshness,
            },
            "rules": {
                "working_window_sec": 90,
                "stalled_window_sec": 600,
                "overdue_window_sec": 240,
            },
            "autoheal": {
                "running": autoheal_running,
                "pid": autoheal_pid,
                "state": heal_state,
            },
            "session": {
                "input_tokens": int(session.get("inputTokens", 0) or 0),
                "output_tokens": int(session.get("outputTokens", 0) or 0),
                "total_tokens": int(session.get("totalTokens", 0) or 0),
                "context_tokens": int(session.get("contextTokens", 0) or 0),
            },
            "can_nudge": True,
        }

    def nudge(self, reason: str = "manual_nudge") -> Tuple[bool, str]:
        cli = self._resolve_cli()
        if not cli:
            return False, "OpenClaw CLI was not found"

        sid = self._main_session_id()
        reply_required = bool(self.monitoring_cfg.get("require_reply_after_nudge", True))
        if reply_required:
            instruction = 'Reply with "收到，正在工作" or "Received, working now" before reporting a concrete action.'
        else:
            instruction = "Start executing the task immediately."

        message = (
            "Agent monitor nudge triggered. "
            f"{instruction} "
            "Then perform one verifiable action and report: current action / output path / next step."
        )
        cmd = [cli, "agent"]
        if sid:
            cmd += ["--session-id", sid]
        cmd += ["--message", message, "--json", "--deliver"]
        env = os.environ.copy()
        cli_bin = str(Path(cli).parent)
        env["PATH"] = f"{cli_bin}:{env.get('PATH', '')}".rstrip(":")

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=180,
                check=False,
                env=env,
            )
            output = (proc.stdout or "").strip()
            NUDGE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with NUDGE_LOG.open("a", encoding="utf-8") as handle:
                handle.write(f"[{_format_ts()}] reason={reason} code={proc.returncode} sid={sid or '-'}\n")
            if proc.returncode == 0:
                return True, "Nudge sent successfully"
            return False, f"Nudge failed: {(output.replace(chr(10), ' ')[:220]) or 'unknown error'}"
        except Exception as exc:
            return False, f"Nudge exception: {exc}"

    def manual_heal(self, reason: str = "manual_heal") -> Tuple[bool, str]:
        gateway_running = _check_process("openclaw-gateway")
        if not gateway_running:
            cli = self._resolve_cli()
            if not cli:
                return False, "OpenClaw CLI was not found"
            env = os.environ.copy()
            cli_bin = str(Path(cli).parent)
            env["PATH"] = f"{cli_bin}:{env.get('PATH', '')}".rstrip(":")
            try:
                proc = subprocess.run(
                    [cli, "gateway", "restart"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=120,
                    check=False,
                    env=env,
                )
                output = (proc.stdout or "").strip()
                if proc.returncode == 0:
                    return True, "Gateway restart triggered"
                return False, f"Heal failed: {(output.replace(chr(10), ' ')[:220]) or 'unknown error'}"
            except Exception as exc:
                return False, f"Heal exception: {exc}"
        return self.nudge(reason=reason)

    def send_message(self, text: str) -> Tuple[bool, str]:
        cli = self._resolve_cli()
        if not cli:
            return False, "OpenClaw CLI was not found"
        sid = self._main_session_id()
        if not sid:
            return False, "No active OpenClaw session found"
        message = text.strip()
        if not message:
            return False, "Message is empty"
        cmd = [cli, "agent", "--session-id", sid, "--message", message, "--json", "--deliver"]
        env = os.environ.copy()
        cli_bin = str(Path(cli).parent)
        env["PATH"] = f"{cli_bin}:{env.get('PATH', '')}".rstrip(":")
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=180,
                check=False,
                env=env,
            )
            output = (proc.stdout or "").strip()
            if proc.returncode == 0:
                return True, "Message sent"
            return False, f"Send failed: {(output.replace(chr(10), ' ')[:220]) or 'unknown error'}"
        except Exception as exc:
            return False, f"Send exception: {exc}"

    def _chat_messages(self) -> List[Dict[str, Any]]:
        path = self._session_jsonl_path()
        if not path:
            return []

        messages: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("type") != "message":
                continue
            message = item.get("message")
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role not in {"user", "assistant", "system"}:
                continue
            content = message.get("content", [])
            text_parts: List[str] = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                        text_parts.append(block["text"])
            text = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
            if not text and isinstance(message.get("errorMessage"), str):
                text = message["errorMessage"]
            if not text:
                continue
            messages.append(
                {
                    "seq": len(messages),
                    "id": item.get("id"),
                    "role": role,
                    "text": text,
                    "timestamp": item.get("timestamp") or message.get("timestamp"),
                    "model": message.get("model"),
                    "stop_reason": message.get("stopReason"),
                }
            )
        return messages

    def chat_history(self, limit: int = 40, before: Optional[int] = None) -> Dict[str, Any]:
        messages = self._chat_messages()
        end = len(messages) if before is None else max(0, min(len(messages), before))
        start = max(0, end - limit)
        window = messages[start:end]
        return {
            "session_id": self._main_session_id(),
            "messages": window,
            "total_messages": len(messages),
            "has_more": start > 0,
            "next_before": start if start > 0 else None,
        }


def _make_adapter(config: Dict[str, Any]) -> OpenClawAdapter:
    agent_type = str(config.get("agent", {}).get("type", "openclaw")).lower()
    if agent_type != "openclaw":
        raise ValueError(f"Unsupported agent type: {agent_type}")
    return OpenClawAdapter(config)


def collect_status() -> Dict[str, Any]:
    config = load_monitor_config()
    adapter = _make_adapter(config)
    agent_status = adapter.collect_status()

    deployment = config.get("deployment", {})
    dashboard = config.get("dashboard", {})
    monitoring = config.get("monitoring", {})

    status_messages = {
        "working": "Agent is actively making progress",
        "thinking": "Agent is online and likely reasoning",
        "idle": "Agent is online and waiting",
        "overdue": "Agent should be working but has gone quiet",
        "stalled": "Agent appears stalled",
        "down": "Agent gateway is not running",
    }

    state = agent_status["state"]
    payload = {
        "timestamp": _format_ts(),
        "agent": {
            "id": agent_status["agent_id"],
            "type": agent_status["agent_type"],
            "name": agent_status["agent_name"],
        },
        "deployment": {
            "mode": deployment.get("mode", "local"),
            "cloud_enabled": bool(deployment.get("cloud_enabled", False)),
            "cloud_url": deployment.get("cloud_url", ""),
        },
        "ui": {
            "locale": config.get("locale", "en-US"),
            "theme": config.get("theme", "crimson"),
            "avatar_style": config.get("avatar_style", "robot"),
            "tap_action": config.get("tap_action", "nudge"),
            "hero_metric": config.get("hero_metric", "score"),
            "supported_locales": ["zh-CN", "en-US", "zh-TW", "ja-JP", "ko-KR", "es-ES"],
            "supported_themes": ["crimson", "ocean", "terminal", "paper", "sunset", "forest", "midnight"],
            "supported_avatar_styles": [
                "robot_classic",
                "robot_guard",
                "robot_soft",
                "robot_square",
                "robot_scout",
                "robot_neon",
                "robot_pearl",
                "robot_mono",
                "robot_sprite",
                "robot_boss",
            ],
            "supported_tap_actions": ["nudge", "refresh", "cycle_theme", "cycle_avatar"],
            "supported_hero_metrics": [
                "score",
                "grade",
                "focus",
                "freshness",
                "throughput",
                "total_tokens",
                "input_tokens",
                "output_tokens",
                "context_tokens",
                "session_age",
                "case_age",
                "log_age",
                "log_delta",
                "gateway_running",
                "app_running",
                "autoheal_running",
            ],
            "widgets": dashboard.get("widgets", []),
            "title": dashboard.get("title", "OpenClaw Dashboard"),
            "subtitle": dashboard.get("subtitle", ""),
        },
        "status": {
            "code": state,
            "message_key": state,
            "message": status_messages.get(state, state),
            "reason_key": agent_status.get("state_reason", "unknown"),
        },
        "metrics": {
            "score": agent_status["score"],
            "meters": agent_status["meters"],
            "session_age_sec": agent_status["session_age_sec"],
            "case_age_sec": agent_status["case_age_sec"],
            "log_age_sec": agent_status["log_age_sec"],
            "log_delta_bytes": agent_status["log_delta_bytes"],
            "tokens": agent_status["session"],
            "grade": _grade_score(agent_status["score"]),
        },
        "monitoring": {
            "should_work": agent_status["should_work"],
            "waiting_external": agent_status["waiting_external"],
            "should_work_reason_key": agent_status["should_work_reason_key"],
            "should_work_reason": agent_status["should_work_reason"],
            "force_work_mode": bool(monitoring.get("force_work_mode", True)),
            "require_reply_after_nudge": bool(monitoring.get("require_reply_after_nudge", True)),
            "force_idle_threshold_sec": int(monitoring.get("force_idle_threshold_sec", 180)),
        },
        "runtime": {
            "gateway_running": agent_status["gateway_running"],
            "app_running": agent_status["app_running"],
            "session_updated_at": agent_status["session_updated_at"],
            "latest_case_file": agent_status["latest_case_file"],
            "latest_task_file": agent_status["latest_task_file"],
            "autoheal": agent_status["autoheal"],
        },
        "actions": {
            "can_nudge": agent_status["can_nudge"],
            "can_heal": True,
        },
        "compat": agent_status,
    }
    _append_history_sample(payload)
    return payload


class MonitorHandler(BaseHTTPRequestHandler):
    def _json(self, payload: Dict[str, Any], code: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _html(self, html: str, code: int = 200) -> None:
        raw = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/", "/index.html"}:
            self._html(INDEX_HTML.read_text(encoding="utf-8"))
            return
        if path == "/api/status":
            self._json(collect_status())
            return
        if path == "/api/history":
            params = parse_qs(parsed.query)
            range_name = params.get("range", ["day"])[0]
            self._json(build_history_payload(range_name))
            return
        if path == "/api/chat/history":
            params = parse_qs(parsed.query)
            limit_raw = params.get("limit", ["40"])[0]
            before_raw = params.get("before", [""])[0]
            try:
                limit = max(1, min(200, int(limit_raw)))
            except ValueError:
                limit = 40
            try:
                before = int(before_raw) if before_raw else None
            except ValueError:
                before = None
            adapter = _make_adapter(load_monitor_config())
            self._json(adapter.chat_history(limit=limit, before=before))
            return
        if path == "/api/config":
            self._json(load_monitor_config())
            return
        self._json({"error": "not found"}, code=404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in {"/api/nudge", "/api/heal", "/api/chat/send"}:
            self._json({"ok": False, "error": "not found"}, code=404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        reason = "manual_nudge"
        text = ""
        if length > 0:
            try:
                raw = self.rfile.read(length).decode("utf-8", errors="ignore")
                payload = json.loads(raw) if raw else {}
                if isinstance(payload, dict) and isinstance(payload.get("reason"), str):
                    reason = payload["reason"][:64]
                if isinstance(payload, dict) and isinstance(payload.get("text"), str):
                    text = payload["text"]
            except Exception:
                pass

        try:
            adapter = _make_adapter(load_monitor_config())
            if path == "/api/heal":
                ok, msg = adapter.manual_heal(reason=reason)
            elif path == "/api/chat/send":
                ok, msg = adapter.send_message(text=text)
            else:
                ok, msg = adapter.nudge(reason=reason)
        except Exception as exc:
            ok, msg = False, str(exc)

        self._json(
            {
                "ok": ok,
                "message": msg,
                "timestamp": _format_ts(),
            },
            code=200 if ok else 500,
        )

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    host = os.getenv("MONITOR_HOST", "127.0.0.1")
    port = int(os.getenv("MONITOR_PORT", "18991"))
    stop_event = threading.Event()
    sampler = threading.Thread(target=_history_sampler, args=(stop_event,), daemon=True)
    sampler.start()
    server = HTTPServer((host, port), MonitorHandler)
    print(f"OpenClaw Dashboard running on http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()
