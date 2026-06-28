#!/usr/bin/env python3
"""
Headless background audio analysis.

Thin wrapper around strip_monitor that forces:
  --no-display       (no matplotlib window)
  --osc-autostart    (stream OSC to receiver immediately)

All other CLI args of strip_monitor.py are forwarded.

Examples:
    python audio_analysis_background.py
    python audio_analysis_background.py --device HK-MIC1
    python audio_analysis_background.py --device 0 --osc-port 9000
    python audio_analysis_background.py --list-devices
"""
import sys
import runpy

extra = []
if "--no-display" not in sys.argv:
    extra.append("--no-display")
if "--osc-autostart" not in sys.argv:
    extra.append("--osc-autostart")

sys.argv = [sys.argv[0]] + sys.argv[1:] + extra
runpy.run_path("strip_monitor.py", run_name="__main__")
