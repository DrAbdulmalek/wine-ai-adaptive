"""Temporary-file hygiene tests for the bottle registry (O_TMPFILE round).

Contract under test (docs/SECURITY_REMEDIATION_STATUS.md §1.2, reviewer-
corrected fail-closed contract 2026-11-13):

- ``_write_temp_bytes`` writes to an UNNAMED inode when O_TMPFILE is
  available (hygiene only — never described as a same-UID closure);
- a MISSING ``O_TMPFILE`` flag raises ``NotSupportedError`` (fail-closed,
  no named fallback) — reviewer test 2;
- a filesystem that REFUSES ``O_TMPFILE`` (ENOTSUP/EINVAL/ENOSYS family,
  proven: overlayfs) also raises ``NotSupportedError`` — there is NO
  named-temporary fallback anywhere (environment-conditional test);
- ``_write_all`` handles partial writes — reviewer test 3;
- during the unnamed write NO named temporary entry exists; the name is
  created only at materialization (reviewer test 4);
- after a successful publication no ``.tmp*`` entries remain (reviewer
  test 8);
- a refused ``linkat`` materialization fails closed and leaves the
  registry untouched (reviewer test 7);
- the mutation witness documents that removing the ``_write_all`` loop
  silently truncates under partial writes (reviewer test 5);
- the code and the contract section make NO CAS / same-UID-closure
  claim (reviewer test 9).

Because the fail-closed contract leaves exactly ONE write path, all
write-flow fixtures sit on ``OTMP_BASE`` (a filesystem that honours
O_TMPFILE: CI ext4, this sandbox's /dev/shm tmpfs). On hosts with no
O_TMPFILE-capable filesystem the flow tests honestly skip; the refusal
tests still run.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from _otmp_support import (
        DEFAULT_TMP_REFUSES_O_TMPFILE,
        OTMP_BASE,
        O_TMPFILE_SUPPORTED,
    )
except ImportError:  # invoked as tests.unit.… — make this dir importable
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _otmp_support import (
        DEFAULT_TMP_REFUSES_O_TMPFILE,
        OTMP_BASE,
        O_TMPFILE_SUPPORTED,
    )
from wine_ai.bottle_manager import registry as registry_module
from wine_ai.bottle_manager.registry import (
    BottleRegistry,
    BottleRegistryError,
    NotSupportedError,
    _write_all,
    _write_temp_bytes,
)


def _directory_supports_o_tmpfile(base: str) -> bool:
    from _otmp_support import _directory_supports_o_tmpfile as _probe
    return _probe(base)


class WriteTempBytesContractTests(unittest.TestCase):
    """Reviewer tests 1-3 and 5 against the module-level helper."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="otmp-hygiene-")
        self.addCleanup(self.tmp.cleanup)

    @unittest.skipUnless(
        O_TMPFILE_SUPPORTED,
        "no O_TMPFILE-capable filesystem available on this host",
    )
    def test_o_tmpfile_used_on_linux(self):
        # Reviewer test 1: spy on os.open and require O_TMPFILE in the
        # flags. Also proves the inode is nameless (nlink 0) and
        # readable via /proc/self/fd.
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
        # Reviewer test 2: without the O_TMPFILE flag the helper refuses
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

    @unittest.skipUnless(
        DEFAULT_TMP_REFUSES_O_TMPFILE,
        "default temp directory honours O_TMPFILE here (refusal flow "
        "covered by the overlayfs-host runs)",
    )
    def test_fail_closed_when_filesystem_refuses_o_tmpfile(self):
        # The reviewer contract extends fail-closed to the REFUSAL case:
        # a filesystem that rejects O_TMPFILE (overlayfs → ENOTSUP) must
        # raise NotSupportedError — NOT degrade to a named temporary.
        # This test runs exactly where the default temp dir refuses the
        # flag (e.g. this overlayfs sandbox); it is skipped where the
        # default temp dir supports it.
        dir_fd = os.open(self.tmp.name, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, dir_fd)
        with self.assertRaises(NotSupportedError):
            _write_temp_bytes(dir_fd, b"payload")
        self.assertEqual(
            os.listdir(self.tmp.name), [],
            "no named temporary file may be created as a fallback",
        )

    def test_partial_writes_handled(self):
        # Reviewer test 3: an os.write that transfers one byte per call
        # must still produce the complete payload — that is _write_all's
        # loop.
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
        # Reviewer test 5 (mutation witness): replacing _write_all with
        # a single non-looping os.write — the "loop removed" mutant —
        # silently truncates under a partial write. This documents why
        # the loop is load-bearing: reviewer test 3 fails against the
        # mutant because the full payload never reaches the file.
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
                "byte lands, so reviewer test 3 (full payload) would fail",
            )


@unittest.skipUnless(
    O_TMPFILE_SUPPORTED,
    "no O_TMPFILE-capable filesystem available for the unnamed flow "
    "(the only write flow after the fail-closed contract)",
)
class AtomicWriteHygieneTests(unittest.TestCase):
    """Reviewer tests 4, 6, 7, 8, 9 through the public register().

    All fixtures sit on ``OTMP_BASE`` so the single fail-closed write
    flow is exercised for real.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="otmp-atomic-", dir=OTMP_BASE)
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

    def _tmp_entries(self) -> list[str]:
        return [n for n in os.listdir(self.root) if n.endswith(".tmp")]

    def test_no_named_temp_before_materialize(self):
        # Reviewer test 4: during the unnamed write NO named temporary
        # entry exists. The spy snapshots the directory at the moment
        # materialization is called — the earliest observation point
        # after the write — and must see no .tmp entry yet. "No temps
        # left after success" alone would be weaker: it cannot prove
        # the payload was written namelessly.
        self.add_bottle_dir("alpha")
        real_link = registry_module.os.link
        observed: list[list[str]] = []

        def spy_link(fobj, name, *args, **kwargs):
            observed.append(sorted(os.listdir(self.root)))
            return real_link(fobj, name, *args, **kwargs)

        with mock.patch("wine_ai.bottle_manager.registry.os.link",
                        side_effect=spy_link):
            self.registry.register(self.make_record("alpha"))

        self.registry.get("alpha")  # publication succeeded
        self.assertEqual(len(observed), 1, "materialization must be called once")
        self.assertFalse(
            any(entry.endswith(".tmp") for entry in observed[0]),
            f"no named temp may exist before materialization; saw {observed[0]}",
        )
        self.assertFalse(self._tmp_entries())

    def test_materialize_then_replace_publishes(self):
        # Reviewer test 6: linkat materialization + os.replace publish
        # the payload end-to-end on a real O_TMPFILE-capable filesystem.
        self.add_bottle_dir("alpha")
        self.add_bottle_dir("beta")
        self.registry.register(self.make_record("alpha"))
        self.registry.register(self.make_record("beta", operation_id="op-2"))
        self.assertEqual(self.registry.get("beta")["operation_id"], "op-2")
        bottles = self.registry.read()
        self.assertIn("alpha", bottles)
        self.assertEqual(bottles["beta"]["operation_id"], "op-2")
        self.assertFalse(self._tmp_entries())

    def test_fail_closed_when_materialize_unsupported(self):
        # Reviewer test 7: a linkat refusal in the unsupported-errno
        # family must raise NotSupportedError (mapped), leave the
        # registry untouched, and NOT fall back to a named temporary.
        self.add_bottle_dir("alpha")
        self.registry.register(self.make_record("alpha"))
        self.add_bottle_dir("beta")
        reg_ino = os.lstat(self.root / "registry.json").st_ino

        def refusing_link(*args, **kwargs):
            raise OSError(95, "Operation not supported")

        with mock.patch("wine_ai.bottle_manager.registry.os.link",
                        side_effect=refusing_link):
            with self.assertRaises(BottleRegistryError):
                self.registry.register(self.make_record("beta", operation_id="op-2"))
        self.assertEqual(os.lstat(self.root / "registry.json").st_ino, reg_ino)
        self.assertEqual(
            sorted(n for n in os.listdir(self.root) if n != "beta"),
            ["alpha", "registry.json", "registry.lock"],
            "no named fallback file may appear after a refused materialization",
        )

    def test_atomic_write_refuses_when_linkat_fails(self):
        # Fail-closed on a non-errno-family linkat failure (EPERM): the
        # write refuses loudly, the registry is untouched, and no
        # temporary entry is left behind.
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
        self.assertFalse(self._tmp_entries())

    def test_no_named_temp_left_after_success(self):
        # Reviewer test 8: after a successful publication no .tmp*
        # entries remain and the published file has exactly mode 0600.
        self.add_bottle_dir("alpha")
        record = self.make_record("alpha")
        self.registry.register(record)
        self.assertEqual(self.registry.get("alpha")["name"], "alpha")
        self.assertFalse(
            self._tmp_entries(),
            "a successful write must leave no temporary files behind",
        )
        info = os.lstat(self.root / "registry.json")
        self.assertEqual((info.st_mode & 0o777), 0o600)

    def test_publication_does_not_claim_cas(self):
        # Reviewer test 9: the implementation and the binding contract
        # section must state the honest limits (positive assertions) and
        # must NOT contain the banned overclaim phrases.
        import pathlib
        import re

        module_src = pathlib.Path(registry_module.__file__).read_text(
            encoding="utf-8"
        )
        status_path = (
            pathlib.Path(registry_module.__file__).resolve().parents[3]
            / "docs" / "SECURITY_REMEDIATION_STATUS.md"
        )
        section = status_path.read_text(encoding="utf-8")

        # Honest-limit markers must be present.
        self.assertIn("makes no same-UID claim", module_src)
        self.assertIn("no portable CAS exists", module_src)
        self.assertIn(
            "cannot prove that the path still refers to the same inode",
            section,
            "the corrected §1.2 open/open-after wording must be present",
        )

        # Banned overclaim phrases must be absent from both files.
        banned = (
            "symlink or entry swap after open is refused",
            "prevents symlink swap",
            "closes the same-UID race",
            "same-UID race is closed",
            "same-UID race resolved",
            "same-UID race fixed",
            "provides a compare-and-swap",
            "provides CAS",
            "CAS-safe publication",
            "atomic CAS publication",
            "is an atomic compare-and-swap",
        )
        for text, label in ((module_src, "registry.py"), (section, "§1.2")):
            for phrase in banned:
                self.assertNotIn(
                    phrase, text,
                    f"{label} must not claim: {phrase!r}",
                )
        # Defensive: no future renameat2/CAS leftovers may describe the
        # ADOPTED publication protocol (rejection discussions in §1.1
        # are allowed to mention it).
        for text, label in ((module_src, "registry.py"),):
            self.assertNotIn("renameat2", text, f"{label} uses os.replace")


@unittest.skipUnless(
    DEFAULT_TMP_REFUSES_O_TMPFILE,
    "default temp directory honours O_TMPFILE here (refusal flow "
    "covered by the overlayfs-host runs)",
)
class DefaultTmpRefusalTests(unittest.TestCase):
    """Pin the fail-closed refusal where the default temp dir refuses.

    On overlayfs hosts (this sandbox: /tmp → ENOTSUP) the registry must
    refuse writes instead of degrading to a named temporary file.
    """

    def test_default_tmp_refusal_fails_closed_no_named_fallback(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="otmp-refuse-")
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        app = home / ".local" / "share" / "wine-ai-adaptive"
        app.mkdir(parents=True, mode=0o700)
        app.chmod(0o700)
        root = app / "prefixes"
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        (home / ".local").chmod(0o700)
        (home / ".local" / "share").chmod(0o700)

        bottle = root / "alpha"
        os.mkdir(bottle, 0o700)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            info = os.stat("alpha", dir_fd=fd)
        finally:
            os.close(fd)
        record = {
            "name": "alpha",
            "relative_path": "prefixes/alpha",
            "state": "EMPTY_UNINITIALIZED",
            "created_at": 1731300000.0,
            "operation_id": "op-1",
            "identity": {"dev": int(info.st_dev), "ino": int(info.st_ino)},
        }

        registry = BottleRegistry(home=home)
        with self.assertRaises(BottleRegistryError) as ctx:
            registry.register(record)
        self.assertIn("fail-closed", str(ctx.exception))
        self.assertIn("fallback is forbidden", str(ctx.exception))
        leftovers = [n for n in os.listdir(root) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [], "no temporary file may survive a refusal")
        self.assertFalse(
            (root / "registry.json").exists(),
            "a refused write must not publish anything",
        )


if __name__ == "__main__":
    unittest.main()
