"""Shared configuration constants."""

import os

SWEEP_INTERVAL_SEC = int(os.getenv("SWEEP_INTERVAL_SEC", "20"))
SWEEP_STUCK_MULTIPLIER = float(os.getenv("SWEEP_STUCK_MULTIPLIER", "3"))
