#!/usr/bin/env python3
"""Origen CLI: provider-neutral Content Origin / Provenance operations."""

from __future__ import annotations

import sys

import origen_core
from origen_engine import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], core=origen_core))
