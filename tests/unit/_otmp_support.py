"""Shared O_TMPFILE capability probing for the registry test suite.

Not collected by unittest discovery (module name does not match
``test_*.py``). Test modules import the constants defined here so every
fixture lands on a filesystem that honours ``O_TMPFILE`` — after the
2026-11-13 fail-closed contract (§1.2) the registry has exactly ONE
write path: the unnamed-inode flow. There is no named fallback to
exercise, so write-flow fixtures must sit on a capable filesystem
(CI ext4, this sandbox's /dev/shm tmpfs) or the flow tests honestly
skip.
"""

from __future__ import annotations

import os
import shutil
import tempfile

O_TMPFILE = getattr(os, "O_TMPFILE", None)


def _directory_supports_o_tmpfile(base: str) -> bool:
    """Probe whether ``base`` lives on a filesystem honouring O_TMPFILE."""
    if O_TMPFILE is None:
        return False
    probe = None
    dir_fd = None
    try:
        probe = tempfile.mkdtemp(prefix="otmp-probe-", dir=base)
        dir_fd = os.open(probe, os.O_RDONLY | os.O_DIRECTORY)
        fd = os.open(".", O_TMPFILE | os.O_WRONLY, 0o600, dir_fd=dir_fd)
        os.close(fd)
        return True
    except OSError:
        return False
    finally:
        if dir_fd is not None:
            os.close(dir_fd)
        if probe is not None:
            shutil.rmtree(probe, ignore_errors=True)


def _probe_o_tmpfile_support() -> bool:
    """Return True if any known writable directory supports O_TMPFILE."""
    if O_TMPFILE is None:
        return False
    for base in ("/dev/shm", tempfile.gettempdir()):
        if not os.path.isdir(base):
            continue
        if _directory_supports_o_tmpfile(base):
            return True
    return False


#: True when some writable directory on this host honours O_TMPFILE.
O_TMPFILE_SUPPORTED = _probe_o_tmpfile_support()

#: True when the DEFAULT temp directory refuses O_TMPFILE while the flag
#: itself exists (proven: overlayfs → ENOTSUP). Used by the environment-
#: conditional test that pins the fail-closed refusal on such hosts.
DEFAULT_TMP_REFUSES_O_TMPFILE = (
    O_TMPFILE is not None
    and not _directory_supports_o_tmpfile(tempfile.gettempdir())
)

#: Where O_TMPFILE-capable tests create their fixtures: prefer the
#: default temp directory when it supports O_TMPFILE (CI ext4), else
#: fall back to /dev/shm (tmpfs) so the unnamed flow is exercised even
#: on hosts whose temp filesystem refuses it (proven: overlayfs).
OTMP_BASE = (
    tempfile.gettempdir()
    if O_TMPFILE_SUPPORTED and _directory_supports_o_tmpfile(tempfile.gettempdir())
    else "/dev/shm"
)
