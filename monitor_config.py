#!/usr/bin/env python3
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"


def _default_config() -> Dict[str, Any]:
    return {
        "locale": "en-US",
        "theme": "crimson",
        "avatar_style": "robot_classic",
        "tap_action": "nudge",
        "hero_metric": "score",
        "deployment": {
            "mode": "local",
            "cloud_url": "",
            "cloud_enabled": False,
        },
        "dashboard": {
            "title": "OpenClaw Dashboard",
            "subtitle": "Local visual operations dashboard for one OpenClaw agent.",
            "widgets": [
                "agent_status",
                "autoheal",
                "chat",
                "efficiency_trend",
            ],
        },
        "agent": {
            "id": "main",
            "type": "openclaw",
            "display_name": "OpenClaw Main",
            "robot_name": "OpenClaw Main",
            "openclaw": {
                "home": str(Path.home() / ".openclaw"),
                "session_key": "agent:main:main",
                "case_roots": [
                    str(Path.home() / "Documents" / "OpenClaw" / "cases"),
                ],
            },
        },
        "monitoring": {
            "force_work_mode": True,
            "require_reply_after_nudge": True,
            "force_idle_threshold_sec": 180,
            "normal_idle_threshold_rounds": 4,
            "overdue_threshold_rounds": 2,
            "soft_heal_cooldown_sec": 180,
            "hard_heal_cooldown_sec": 420,
            "allow_hard_restart": False,
        },
    }


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _normalize_legacy_keys(raw: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    monitoring = cfg["monitoring"]
    dashboard = cfg["dashboard"]

    mapping = {
        "老板强推模式": ("force_work_mode", bool),
        "提醒后必须回报": ("require_reply_after_nudge", bool),
        "强推静默阈值秒": ("force_idle_threshold_sec", int),
        "普通空转阈值轮数": ("normal_idle_threshold_rounds", int),
        "应工作未动阈值轮数": ("overdue_threshold_rounds", int),
        "软修复冷却秒": ("soft_heal_cooldown_sec", int),
        "硬修复冷却秒": ("hard_heal_cooldown_sec", int),
        "允许硬重启": ("allow_hard_restart", bool),
    }

    for legacy_key, (new_key, caster) in mapping.items():
        if legacy_key in raw:
            try:
                monitoring[new_key] = caster(raw[legacy_key])
            except Exception:
                pass

    if isinstance(raw.get("语言"), str):
        cfg["locale"] = raw["语言"]
    if isinstance(raw.get("主题"), str):
        cfg["theme"] = raw["主题"]
    if isinstance(raw.get("形象"), str):
        cfg["avatar_style"] = "robot_classic" if raw["形象"] == "robot" else raw["形象"]
    if isinstance(raw.get("拍头动作"), str):
        cfg["tap_action"] = raw["拍头动作"]
    if isinstance(raw.get("主数值"), str):
        cfg["hero_metric"] = raw["主数值"]
    if isinstance(raw.get("机器人名字"), str):
        cfg["agent"]["robot_name"] = raw["机器人名字"]
    if isinstance(raw.get("监控卡片"), list):
        dashboard["widgets"] = [str(item) for item in raw["监控卡片"]]


def load_monitor_config() -> Dict[str, Any]:
    cfg = _default_config()
    if not CONFIG_PATH.exists():
        return cfg

    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return cfg

    if not isinstance(raw, dict):
        return cfg

    _deep_merge(cfg, deepcopy(raw))
    _normalize_legacy_keys(raw, cfg)
    return cfg
