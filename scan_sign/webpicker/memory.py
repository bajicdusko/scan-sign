"""Handing freed memory back to the kernel.

Rendering a page is by far the largest allocation this process makes — a 200 DPI raster and the
float arrays scanify builds on top of it run to hundreds of megabytes per page. Python releases
all of it the moment the render returns, but free() only hands the blocks to glibc's own free
lists. glibc then raises its mmap threshold at runtime once it has seen a few large blocks freed,
so subsequent ones come off the heap instead and are only returned to the OS if they happen to sit
at its very top. They rarely do, and on a container with a hard memory cap that reads as a leak:
RSS ratchets up across renders and never comes back down.

malloc_trim asks glibc to madvise those free pages away now. Off glibc — macOS in development,
musl — there is nothing to call and this is a no-op.
"""

from __future__ import annotations

import ctypes
import ctypes.util


def _resolve_malloc_trim():
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
        trim = libc.malloc_trim
    except (OSError, AttributeError):
        return None
    trim.argtypes = [ctypes.c_size_t]
    trim.restype = ctypes.c_int
    return trim


_malloc_trim = _resolve_malloc_trim()


def release_free_memory() -> None:
    """Return the allocator's free pages to the kernel; costs a few milliseconds."""
    if _malloc_trim is not None:
        _malloc_trim(0)
