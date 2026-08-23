#!/usr/bin/env python3
"""Runtime adapter for atomic no-replace directory publication."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import sys


def rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish source without replacing an existing destination."""
    libc = ctypes.CDLL(None, use_errno=True)
    source_b, destination_b = os.fsencode(source), os.fsencode(destination)
    if sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        result = libc.renamex_np(source_b, destination_b, 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, source_b, -100, destination_b, 1)  # RENAME_NOREPLACE
    else:
        raise NotImplementedError("runtime lacks an atomic no-replace directory adapter")
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error_number, os.strerror(error_number), str(destination))
    raise OSError(error_number, os.strerror(error_number), str(destination))
