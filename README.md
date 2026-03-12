# VISEA for OpenClaw

VISEA is a local-first operations console for OpenClaw. It gives a single agent a vivid face, a clean live dashboard, an efficiency trend, a chat panel, and optional auto-heal controls in one page.

This build is the polished `v1.0.0` single-agent release candidate. It is designed to feel lovable on the surface and useful underneath.

## What It Does

- Shows whether your OpenClaw agent is working, thinking, idle, overdue, stalled, or down
- Tracks an efficiency score with trend history and token visibility
- Lets you nudge the agent manually from the dashboard
- Supports optional auto-heal logic for long silent periods
- Adds a customizable avatar surface with expressions, headwear, hands, wings, and ambient effects

## Why It Exists

Most agent dashboards are either too raw, too noisy, or too technical for daily use.

VISEA is aimed at a different feeling:

- local-first
- low-noise
- visually expressive
- easy to leave open all day

The goal is not just to expose system state, but to make one OpenClaw agent feel legible, alive, and easy to supervise.

## Current Scope

- Single-agent local dashboard
- macOS-focused launch flow
- Local OpenClaw session and gateway inspection
- Trend chart, activity feed, and direct chat panel
- Optional watchdog and auto-heal helpers

## Local Access

- Dashboard: `http://127.0.0.1:18991/`

## Services

- `com.studywest.openclaw.arcade-monitor`
- `com.studywest.openclaw.arcade-autoheal`
- `com.studywest.openclaw.app-watchdog`

Useful checks:

```bash
launchctl print gui/$(id -u)/com.studywest.openclaw.arcade-monitor | rg 'state =|pid =|last exit code'
launchctl print gui/$(id -u)/com.studywest.openclaw.arcade-autoheal | rg 'state =|pid =|last exit code'
launchctl print gui/$(id -u)/com.studywest.openclaw.app-watchdog | rg 'state =|pid =|last exit code'
```

## Key Files

- `server.py`
- `monitor_config.py`
- `config.example.json`
- `install_launchd.sh`
- `PRODUCT_DIRECTION.md`
- `MARKET_LISTING.md`
- `GITHUB_RELEASE_COPY.md`
- `SCREENSHOT_PLAN.md`

## Configuration

Main configuration lives in `config.json`.

Important controls include:

- force work mode
- idle thresholds
- required reply after nudge
- auto-heal behavior
- theme and avatar defaults

## Known Limits

- Currently optimized for macOS local use
- Assumes a local OpenClaw installation and gateway
- Not yet prepared for multi-agent routing or team accounts
- Release visuals still need final screenshot capture

## Release Direction

This repository is being prepared for:

1. GitHub publication
2. ClawHub listing
3. future multi-agent expansion

The current product direction is documented in `PRODUCT_DIRECTION.md`.
