"""Temporary-file hygiene tests for the bottle registry (O_TMPFILE round).

Contract under test (docs/SECURITY_REMEDIATION_STATUS.md §1.2, item 3 of
the 2026-11-13 unified plan):

- ``_write_temp_bytes`` writes to an UNNAMED inode when O_TMPFILE is
  available (hygiene only — never described as a same-UID closure);
- a missing ``O_TMPFILE`` flag raises ``NotSupportedError`` (fail-closed,
  no named fallback) — plan test 2;
- ``_write_all`` handles partial writes — plan test 3;
- after a successful publication no ``.tmp*`` entries remain — plan test 4;
- the mutation witness documents that removing the ``_write_all`` loop
  silently truncates under partial writes, which is exactly what plan
  test 3 pins — plan test 5.

Tests that need real O_TMPFILE support run on ``/dev/shm`` (tmpfs) when
the default temp filesystem refuses it (proven: overlayfs → ENOTSUP);
the suite therefore exercises BOTH documented flows: the unnamed flow
here on tmpfs, and the hardened named-temporary degradation everywhere
else. The plan's tests 1 and 5 fail when the fix is removed.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wine_ai.bottle_manager import registry as registry_module
from wine_ai.bottle_manager.registry import (
    BottleRegistry,
    BottleRegistryError,
    NotSupportedError,
    _write_all,
    _write_temp_bytes,
)

O_TMPFILE = getattr(os, "O_TMPFILE", None)


def _probe_o_tmpfile_support() -> bool:
    """Return True if some writable directory supports O_TMPFILE."""
    if O_TMPFILE is None:
        return False
    for base in ("/dev/shm", tempfile.gettempdir()):
        if not os.path.isdir(base):
            continue
        probe = None
        dir_fd = None
        try:
            probe = tempfile.mkdtemp(prefix="otmp-probe-", dir=base)
            dir_fd = os.open(probe, os.O_RDONLY | os.O_DIRECTORY)
            fd = os.open(".", O_TMPFILE | os.O_WRONLY, 0o600, dir_fd=dir_fd)
            os.close(fd)
            return True
        except OSError:
            continue
        finally:
            if dir_fd is not None:
                os.close(dir_fd)
            if probe is not None:
                shutil.rmtree(probe, ignore_errors=True)
    return False


O_TMPFILE_SUPPORTED = _probe_o_tmpfile_support()


def _directory_supports_o_tmpfile(base: str) -> bool:
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


#: Where O_TMPFILE-capable tests create their fixtures: prefer the
#: default temp directory when it supports O_TMPFILE (CI ext4), else
#: fall back to /dev/shm (tmpfs) so the unnamed flow is exercised even
#: on hosts whose temp filesystem refuses it (proven: overlayfs).
OTMP_BASE = (
    tempfile.gettempdir()
    if _directory_supports_o_tmpfile(tempfile.gettempdir())
    else "/dev/shm"
)


class WriteTempBytesContractTests(unittest.TestCase):
    """Plan tests 1-3 and 5 against the module-level helper."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="otmp-hygiene-")
        self.addCleanup(self.tmp.cleanup)

    def test_o_tmpfile_used_on_linux(self):
        # Plan test 1: spy on os.open and require O_TMPFILE in the flags.
        # Also proves the inode is nameless (nlink 0) and readable via
        # /proc/self/fd. Skipped only where no filesystem honours
        # O_TMPFILE (documented degradation — see §1.2).
        if not _directory_supports_o_tmpfile(OTMP_BASE):
            self.skipTest("filesystem refuses O_TMPFILE (documented degradation)")
        otmp_dir = tempfile.TemporaryDirectory(prefix="otmp-spy-", dir=OTMP_BASE)
        self.addCleanup(otmp_dir.cleanup)
        dir_fd = os.open(otmp_dir.name, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, dir_fd)
        captured: list[int] = []
        real_open = os.open

        def spy_open(path, flags, *args, **kwargs):
            captured.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch("wine_ai.bottle_manager.registry.os.open",
                        side_effect=spy_open):
            fd = _write_temp_bytes(dir_fd, b"payload")
        self.addCleanup(os.close, fd)

        self.assertTrue(captured, "os.open must be called for the temp inode")
        self.assertIsNotNone(registry_module._O_TMPFILE)
        self.assertTrue(
            captured[0] & registry_module._O_TMPFILE,
            "O_TMPFILE flag missing from the temporary inode open",
        )
        self.assertTrue(captured[0] & os.O_WRONLY)
        # Unnamed while held: zero hard links until publication.
        self.assertEqual(os.fstat(fd).st_nlink, 0)
        with open(f"/proc/self/fd/{fd}", "rb") as handle:
            self.assertEqual(handle.read(), b"payload")

    def test_fail_closed_when_o_tmpfile_missing(self):
        # Plan test 2: without the O_TMPFILE flag the helper refuses
        # (fail-closed) and creates NO named fallback file.
        dir_fd = os.open(self.tmp.name, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, dir_fd)
        with mock.patch.object(registry_module, "_O_TMPFILE", None):
            with self.assertRaises(NotSupportedError):
                _write_temp_bytes(dir_fd, b"payload")
        self.assertEqual(
            os.listdir(self.tmp.name), [],
            "no named temporary file may be created as a fallback",
        )

    def test_partial_writes_handled(self):
        # Plan test 3: an os.write that transfers one byte per call must
        # still produce the complete payload — that is _write_all's loop.
        path = os.path.join(self.tmp.name, "plain.tmp")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.addCleanup(os.close, fd)
        real_write = os.write
        payload = b"x" * 64 + b"y"
        calls = {"count": 0}

        def one_byte_write(fdesc, buf):
            calls["count"] += 1
            return real_write(fdesc, buf[:1])

        with mock.patch("wine_ai.bottle_manager.registry.os.write",
                        side_effect=one_byte_write):
            _write_all(fd, payload)

        self.assertEqual(calls["count"], len(payload))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), payload)

    def test_mutation_removing_write_all_fails_partial_write_guard(self):
        # Plan test 5 (mutation witness): replacing _write_all with a
        # single non-looping os.write — the "loop removed" mutant —
        # silently truncates under a partial write. This documents why
        # the loop is load-bearing: plan test 3 fails against the mutant
        # because the full payload never reaches the file.
        path = os.path.join(self.tmp.name, "mutant.tmp")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.addCleanup(os.close, fd)
        payload = b"z" * 40

        def mutant_write_all(fdesc, data):
            # Mutation: loop removed — one os.write, return ignored,
            # partial writes never retried.
            registry_module.os.write(fdesc, data)

        real_write = os.write

        def one_byte_write(fdesc, buf):
            return real_write(fdesc, buf[:1])

        with mock.patch.object(registry_module, "_write_all",
                               mutant_write_all):
            with mock.patch("wine_ai.bottle_manager.registry.os.write",
                            side_effect=one_byte_write):
                registry_module._write_all(fd, payload)

        with open(path, "rb") as handle:
            self.assertEqual(
                handle.read(), payload[:1],
                "mutation evidence: without the write loop only the first "
                "byte lands, so plan test 3 (full payload) would fail",
            )


class AtomicWriteHygieneTests(unittest.TestCase):
    """Plan test 4 plus flow-coverage extras through the public register().

    Subclasses choose the fixture filesystem: the default temp directory
    (documented named-temp degradation on overlayfs hosts, unnamed flow
    on CI ext4) or ``OTMP_BASE`` (forced unnamed flow).
    """

    unnamed = False

    def setUp(self):
        base = OTMP_BASE if self.unnamed else None
        self.tmp = tempfile.TemporaryDirectory(prefix="otmp-atomic-", dir=base)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        app = self.home / ".local" / "share" / "wine-ai-adaptive"
        app.mkdir(parents=True, mode=0o700)
        app.chmod(0o700)
        self.root = app / "prefixes"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        (self.home / ".local").chmod(0o700)
        (self.home / ".local" / "share").chmod(0o700)
        self.registry = BottleRegistry(home=self.home)

    def add_bottle_dir(self, name: str) -> None:
        os.mkdir(name, 0o700, dir_fd=os.open(self.root, os.O_RDONLY | os.O_DIRECTORY))

    def make_record(self, name: str, *, operation_id: str = "op-1") -> dict:
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            info = os.stat(name, dir_fd=fd)
        finally:
            os.close(fd)
        return {
            "name": name,
            "relative_path": f"prefixes/{name}",
            "state": "EMPTY_UNINITIALIZED",
            "created_at": 1731300000.0,
            "operation_id": operation_id,
            "identity": {"dev": int(info.st_dev), "ino": int(info.st_ino)},
        }

    def test_no_named_temp_created(self):
        # Plan test 4: after a successful publication, no .tmp* entries
        # remain — true in the unnamed flow (name existed only moments)
        # and in the documented named-temp degradation.
        self.add_bottle_dir("alpha")
        record = self.make_record("alpha")
        self.registry.register(record)
        self.assertEqual(self.registry.get("alpha")["name"], "alpha")
        self.assertFalse(
            any(name.endswith(".tmp") for name in os.listdir(self.root)),
            "a successful write must leave no temporary files behind",
        )
        info = os.lstat(self.root / "registry.json")
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)

    @unittest.skipUnless(
        O_TMPFILE_SUPPORTED,
        "no O_TMPFILE-capable filesystem available for the unnamed flow",
    )
    def test_unnamed_flow_publication_round_trip(self):
        # The unnamed flow on an O_TMPFILE-capable filesystem: register,
        # replace, and read back all succeed and no .tmp* survives.
        self.add_bottle_dir("alpha")
        self.add_bottle_dir("beta")
        self.registry.register(self.make_record("alpha"))
        self.registry.register(self.make_record("beta", operation_id="op-2"))
        self.assertEqual(self.registry.get("beta")["operation_id"], "op-2")
        # read() returns the bottles mapping itself.
        bottles = self.registry.read()
        self.assertIn("alpha", bottles)
        self.assertEqual(bottles["beta"]["operation_id"], "op-2")
        self.assertFalse(any(
            name.endswith(".tmp") for name in os.listdir(self.root)
        ))


class UnnamedFlowTests(AtomicWriteHygieneTests):
    """Same suite body, fixtures forced onto the O_TMPFILE-capable base."""

    unnamed = True

    @unittest.skipUnless(
        O_TMPFILE_SUPPORTED,
        "no O_TMPFILE-capable filesystem available for the unnamed flow",
    )
    def test_atomic_write_refuses_when_linkat_fails(self):
        # Fail-closed: if the unnamed inode cannot be attached to a name
        # (linkat refuses), the write refuses loudly, the registry is
        # untouched, and no temporary entry is left behind.
        self.add_bottle_dir("alpha")
        self.add_bottle_dir("beta")
        self.registry.register(self.make_record("alpha"))
        reg_ino = os.lstat(self.root / "registry.json").st_ino

        def refusing_link(*args, **kwargs):
            raise OSError(1, "Operation not permitted")

        with mock.patch("wine_ai.bottle_manager.registry.os.link",
                        side_effect=refusing_link):
            with self.assertRaises(BottleRegistryError):
                self.registry.register(self.make_record("beta", operation_id="op-2"))
        self.assertEqual(os.lstat(self.root / "registry.json").st_ino, reg_ino)
        self.assertFalse(any(
            name.endswith(".tmp") for name in os.listdir(self.root)
        ))


if __name__ == "__main__":
    unittest.main()
