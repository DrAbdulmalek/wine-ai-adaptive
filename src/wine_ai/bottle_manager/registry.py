"""Fail-closed managed registry for Wine bottle records.

Companion to :mod:`wine_ai.bottle_manager.create`: records the identity
of managed bottle directories created under the current user's managed
root and revalidates that identity on every access.

Security contract (bottle-manager integration, Phase B):

- every file open uses ``O_NOFOLLOW`` and is revalidated with ``fstat``
  (regular file, hard-link count 1, owner is the current user, mode
  exactly 0600) — anomalies are rejected, never silently repaired;
- the registry file is written atomically: a unique temporary name is
  created with ``O_CREAT|O_EXCL`` in the registry directory, fsynced,
  then ``os.replace``d over the registry, followed by a directory
  fsync, so no crash leaves a half-written registry behind;
- cooperating processes serialize registry read-modify-write cycles
  through an advisory ``fcntl.flock`` on a dedicated lock file; the
  fixed-name temporary file race of the rejected 2026-11-11 draft is
  eliminated by a unique per-write temporary name;
- the file identity observed at the start of a write cycle is checked
  again after the temporary file is durable and immediately before replace;
  this narrows (but cannot eliminate) a same-uid namespace race because
  POSIX rename has no compare-and-swap-by-inode primitive;
- directory entries are addressed exclusively through directory file
  descriptors with ``follow_symlinks=False``; names never traverse the
  filesystem by themselves;
- failures are fail-closed: the registry raises instead of guessing,
  and never deletes, repairs, or re-creates anything.

Documented residual (honest limit, same class as SEC-01): identity
revalidation compares ``(dev, ino)``; a same-filesystem swap that
recycles the exact inode (observed when ``rmdir`` is immediately
followed by ``mkdir`` on ext4/tmpfs) is indistinguishable from the
original entry. The advisory lock serializes only cooperating writers. A hostile process
running under the same UID can still mutate directory entries and race the
final check-to-rename interval; the current implementation does not claim
protection against that actor. The mode and symlink checks reduce exposure
but are not a same-UID isolation boundary.

Deliberately out of scope (owner disposition, 2026-11-11 independent
review of the rejected ``feature/bottle-manager`` draft): bottle
deletion, Wine execution/initialization, and any automatic cleanup.
The registry is an append/reconcile-only view of managed state.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import stat
import uuid
from pathlib import Path
from typing import Any

from wine_ai.bottle_manager.create import MANAGED_ROOT_PARTS, NAME_PATTERN

__all__ = [
    "BottleRegistry",
    "BottleRegistryError",
    "REGISTRY_NAME",
    "LOCK_NAME",
    "REGISTRY_VERSION",
    "ALLOWED_RECORD_KEYS",
    "REQUIRED_RECORD_KEYS",
    "STATE_EMPTY_UNINITIALIZED",
]

REGISTRY_NAME = "registry.json"
LOCK_NAME = "registry.lock"
REGISTRY_VERSION = 1

#: The only state a registry-tracked bottle may carry. Initialization,
#: execution and removal are separate, later-reviewed features; the
#: registry therefore has no transition vocabulary yet.
STATE_EMPTY_UNINITIALIZED = "EMPTY_UNINITIALIZED"

REQUIRED_RECORD_KEYS = ("name", "relative_path", "state", "created_at", "operation_id", "identity")
ALLOWED_RECORD_KEYS = frozenset(REQUIRED_RECORD_KEYS)
_REGISTRY_TOP_KEYS = frozenset({"registry_version", "bottles"})
_RECORD_IDENTITY_KEYS = frozenset({"dev", "ino"})

_DIR_FLAGS_EXTRA = getattr(os, "O_CLOEXEC", 0)


class BottleRegistryError(Exception):
    """A registry request was rejected or could not be completed safely."""


def _file_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise BottleRegistryError("Required no-follow file flags are unavailable")
    return os.O_NOFOLLOW | _DIR_FLAGS_EXTRA


def _verify_file_identity(info: os.stat_result, *, what: str) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise BottleRegistryError(f"{what} is not a regular file")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise BottleRegistryError(f"{what} is owned by a different user")
    if info.st_nlink != 1:
        raise BottleRegistryError(f"{what} must be a singly-linked file")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise BottleRegistryError(f"{what} permissions must be exactly 0600")


def _identity_of(info: os.stat_result) -> dict[str, int]:
    return {"dev": int(info.st_dev), "ino": int(info.st_ino)}


def _identity_matches(info: os.stat_result, identity: dict[str, int]) -> bool:
    try:
        return (info.st_dev, info.st_ino) == (int(identity["dev"]), int(identity["ino"]))
    except (KeyError, TypeError, ValueError):
        return False


class BottleRegistry:
    """Registry of managed bottle directories under the managed root.

    The managed root is bound to the current user's home directory by
    contract (same root as :func:`wine_ai.bottle_manager.create.create_bottle`).
    """

    def __init__(self, home: Path | str | None = None):
        self._home = Path.home() if home is None else Path(home)

    # -- descriptor plumbing ---------------------------------------------

    def _open_root(self) -> int:
        """Open the managed root with full chain verification, no creation."""
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | _DIR_FLAGS_EXTRA
        if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
            raise BottleRegistryError("Required no-follow directory flags are unavailable")
        absolute = os.path.abspath(os.fspath(self._home))
        fd = os.open("/", flags)
        try:
            for component in Path(absolute).parts[1:]:
                before = os.stat(component, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                    raise BottleRegistryError("Unsafe or non-directory home path component")
                nxt = os.open(component, flags, dir_fd=fd)
                try:
                    after = os.fstat(nxt)
                    if not stat.S_ISDIR(after.st_mode) or (before.st_dev, before.st_ino) != (
                        after.st_dev,
                        after.st_ino,
                    ):
                        raise BottleRegistryError("Home path component identity changed")
                except Exception:
                    os.close(nxt)
                    raise
                os.close(fd)
                fd = nxt
            for component in MANAGED_ROOT_PARTS:
                try:
                    before = os.stat(component, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    raise
                if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                    raise BottleRegistryError("Managed root component is not a real directory")
                nxt = os.open(component, flags, dir_fd=fd)
                try:
                    after = os.fstat(nxt)
                    if not stat.S_ISDIR(after.st_mode) or (before.st_dev, before.st_ino) != (
                        after.st_dev,
                        after.st_ino,
                    ):
                        raise BottleRegistryError("Managed root component identity changed")
                except Exception:
                    os.close(nxt)
                    raise
                os.close(fd)
                fd = nxt
            return fd
        except Exception:
            os.close(fd)
            raise

    def _open_lock(self, root_fd: int) -> int:
        nofollow = _file_flags()
        try:
            fd = os.open(LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=root_fd)
            created = True
        except FileExistsError:
            try:
                fd = os.open(LOCK_NAME, os.O_RDWR | nofollow, dir_fd=root_fd)
                created = False
            except OSError as exc:
                raise BottleRegistryError(f"Could not open registry lock ({exc.errno})") from exc
        except OSError as exc:
            raise BottleRegistryError(f"Could not open registry lock ({exc.errno})") from exc
        try:
            if created:
                os.fchmod(fd, 0o600)
            info = os.fstat(fd)
            _verify_file_identity(info, what="Registry lock")
            return fd
        except Exception:
            os.close(fd)
            raise

    def _open_registry(self, root_fd: int) -> tuple[Any | None, tuple[int, int] | None]:
        """Open the registry file; return (fd, identity) or (None, None) if absent."""
        flags = os.O_RDONLY | _file_flags()
        try:
            fd = os.open(REGISTRY_NAME, flags, dir_fd=root_fd)
        except FileNotFoundError:
            return None, None
        except OSError as exc:
            raise BottleRegistryError(f"Could not open registry file ({exc.errno})") from exc
        try:
            info = os.fstat(fd)
            _verify_file_identity(info, what="Registry file")
            return fd, (info.st_dev, info.st_ino)
        except Exception:
            os.close(fd)
            raise

    # -- validation --------------------------------------------------------

    @staticmethod
    def _validate_record(name: str, record: Any) -> None:
        if NAME_PATTERN.fullmatch(name) is None:
            raise BottleRegistryError(f"Registry contains an invalid bottle name: {name!r}")
        if not isinstance(record, dict):
            raise BottleRegistryError(f"Registry record must be a JSON object: {name}")
        unsupported = set(record) - ALLOWED_RECORD_KEYS
        if unsupported:
            raise BottleRegistryError(
                f"Registry record contains unsupported keys: {sorted(unsupported)}"
            )
        missing = [key for key in REQUIRED_RECORD_KEYS if key not in record]
        if missing:
            raise BottleRegistryError(f"Registry record is missing keys: {missing}")
        if record["name"] != name:
            raise BottleRegistryError(f"Registry record name does not match its key: {name}")
        if record["state"] != STATE_EMPTY_UNINITIALIZED:
            raise BottleRegistryError(f"Registry record carries an unsupported state: {name}")
        if not isinstance(record["created_at"], (int, float)) or isinstance(record["created_at"], bool):
            raise BottleRegistryError(f"Registry record created_at must be a number: {name}")
        for key in ("relative_path", "operation_id"):
            if not isinstance(record[key], str) or not record[key]:
                raise BottleRegistryError(f"Registry record {key} must be a non-empty string: {name}")
        identity = record["identity"]
        if not isinstance(identity, dict) or set(identity) != _RECORD_IDENTITY_KEYS:
            raise BottleRegistryError(f"Registry record identity is malformed: {name}")
        for key in _RECORD_IDENTITY_KEYS:
            if not isinstance(identity[key], int) or isinstance(identity[key], bool) or identity[key] < 0:
                raise BottleRegistryError(f"Registry record identity {key} must be a non-negative int: {name}")

    @classmethod
    def _validate_document(cls, data: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(data, dict):
            raise BottleRegistryError("Registry document must be a JSON object")
        unsupported = set(data) - _REGISTRY_TOP_KEYS
        if unsupported:
            raise BottleRegistryError(f"Registry document contains unsupported keys: {sorted(unsupported)}")
        if data.get("registry_version") != REGISTRY_VERSION:
            raise BottleRegistryError(
                f"Registry version mismatch: expected {REGISTRY_VERSION}"
            )
        bottles = data.get("bottles")
        if not isinstance(bottles, dict):
            raise BottleRegistryError("Registry document must contain a bottles object")
        for name, record in bottles.items():
            cls._validate_record(name, record)
        return data

    # -- atomic persistence -------------------------------------------------

    def _atomic_write(self, root_fd: int, data: dict[str, Any], expected: tuple[int, int] | None) -> None:
        """Replace the registry atomically, revalidating the observed identity."""
        try:
            current = os.stat(REGISTRY_NAME, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        except OSError as exc:
            raise BottleRegistryError(f"Could not re-examine registry file ({exc.errno})") from exc
        if current is not None and stat.S_ISLNK(current.st_mode):
            raise BottleRegistryError("Registry file is a symbolic link")
        actual = None if current is None else (current.st_dev, current.st_ino)
        if actual != expected:
            raise BottleRegistryError("Registry file changed during the operation; write refused")
        payload = (json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")
        tmp_name = f"{REGISTRY_NAME}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _file_flags()
        fd = None
        try:
            fd = os.open(tmp_name, flags, 0o600, dir_fd=root_fd)
            os.fchmod(fd, 0o600)
            info = os.fstat(fd)
            _verify_file_identity(info, what="Temporary registry file")
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError(errno.EIO, "registry write made no progress")
                view = view[written:]
            os.fsync(fd)

            # Revalidate that the temporary name still refers to the inode
            # held open by fd. The random name reduces discoverability but
            # is not a substitute for checking the directory entry.
            tmp_opened = os.fstat(fd)
            tmp_entry = os.stat(tmp_name, dir_fd=root_fd, follow_symlinks=False)
            _verify_file_identity(tmp_entry, what="Temporary registry entry")
            if (tmp_entry.st_dev, tmp_entry.st_ino) != (
                tmp_opened.st_dev, tmp_opened.st_ino
            ):
                raise BottleRegistryError("Temporary registry entry changed before replace")

            # Revalidate the target entry after the temporary file is fully
            # written and synced. This closes the earlier, avoidable gap; it
            # is not an atomic compare-and-swap against hostile same-uid writers.
            try:
                current = os.stat(REGISTRY_NAME, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                current = None
            except OSError as exc:
                raise BottleRegistryError(
                    f"Could not revalidate registry immediately before replace ({exc.errno})"
                ) from exc
            if current is not None:
                _verify_file_identity(current, what="Registry target")
            actual = None if current is None else (current.st_dev, current.st_ino)
            if actual != expected:
                raise BottleRegistryError(
                    "Registry identity changed immediately before replace; write refused"
                )
            os.replace(tmp_name, REGISTRY_NAME, src_dir_fd=root_fd, dst_dir_fd=root_fd)
            os.close(fd)
            fd = None
            os.fsync(root_fd)
        except OSError as exc:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.unlink(tmp_name, dir_fd=root_fd)
            except OSError:
                pass
            raise BottleRegistryError(f"Could not persist registry ({exc.errno})") from exc
        except Exception:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.unlink(tmp_name, dir_fd=root_fd)
            except OSError:
                pass
            raise

    # -- read-modify-write cycles ---------------------------------------------

    def read(self) -> dict[str, dict[str, Any]]:
        """Read a stable registry inode without creating locks or other files.

        Writers publish complete snapshots with os.replace(). Opening the
        registry once pins either the old or new inode, so a reader can parse
        a consistent snapshot without taking the advisory writer lock. This
        keeps list/get/verify/orphans genuinely read-only.
        """
        root_fd = reg_fd = None
        try:
            try:
                root_fd = self._open_root()
            except FileNotFoundError:
                return {}
            reg_fd, _ = self._open_registry(root_fd)
            if reg_fd is None:
                return {}
            raw = bytearray()
            while True:
                chunk = os.read(reg_fd, 65536)
                if not chunk:
                    break
                raw.extend(chunk)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise BottleRegistryError("Registry file is corrupted") from exc
            return dict(self._validate_document(data)["bottles"])
        finally:
            for fd in (reg_fd, root_fd):
                if fd is not None:
                    os.close(fd)

    def register(self, record: dict[str, Any]) -> dict[str, Any]:
        """Append one validated bottle record; idempotent for an identical entry."""
        candidate = dict(record)
        name = candidate.get("name")
        if not isinstance(name, str) or NAME_PATTERN.fullmatch(name) is None:
            raise BottleRegistryError("Invalid bottle name for registration")
        self._validate_record(name, candidate)
        root_fd = lock_fd = reg_fd = None
        try:
            try:
                root_fd = self._open_root()
            except FileNotFoundError as exc:
                raise BottleRegistryError("Managed root does not exist; nothing to register") from exc
            lock_fd = self._open_lock(root_fd)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                reg_fd, identity = self._open_registry(root_fd)
                data: dict[str, Any]
                if reg_fd is None:
                    data = {"registry_version": REGISTRY_VERSION, "bottles": {}}
                else:
                    raw = b""
                    while True:
                        chunk = os.read(reg_fd, 65536)
                        if not chunk:
                            break
                        raw += chunk
                    try:
                        data = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, ValueError) as exc:
                        raise BottleRegistryError("Registry file is corrupted") from exc
                    data = self._validate_document(data)
                bottles = data["bottles"]
                if name in bottles:
                    if bottles[name] == candidate:
                        return dict(candidate)
                    raise BottleRegistryError(f"Bottle is already registered with a different record: {name}")
                bottles[name] = candidate
                self._atomic_write(root_fd, data, identity)
                return dict(candidate)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            for fd in (reg_fd, lock_fd, root_fd):
                if fd is not None:
                    os.close(fd)

    def get(self, name: str) -> dict[str, Any]:
        if not isinstance(name, str) or NAME_PATTERN.fullmatch(name) is None:
            raise BottleRegistryError(f"Invalid bottle name: {name!r}")
        bottles = self.read()
        if name not in bottles:
            raise BottleRegistryError(f"Unknown bottle: {name}")
        return dict(bottles[name])

    # -- reconciliation --------------------------------------------------------

    def pin_identity(self, name: str) -> dict[str, int]:
        """Pin the ``(dev, ino)`` identity of a directory under the managed root.

        Used right after :func:`wine_ai.bottle_manager.create.create_bottle`
        to bind the freshly created directory to a registry record. Raises
        :class:`BottleRegistryError` if the entry is not a private (0700)
        real directory.
        """
        if not isinstance(name, str) or NAME_PATTERN.fullmatch(name) is None:
            raise BottleRegistryError(f"Invalid bottle name: {name!r}")
        root_fd = None
        try:
            root_fd = self._open_root()
            try:
                info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise BottleRegistryError(f"Directory does not exist under the managed root: {name}") from exc
            except OSError as exc:
                raise BottleRegistryError(f"Could not examine bottle entry ({exc.errno})") from exc
            if stat.S_ISLNK(info.st_mode):
                raise BottleRegistryError(f"Managed entry is a symbolic link: {name}")
            if not stat.S_ISDIR(info.st_mode):
                raise BottleRegistryError(f"Managed entry is not a directory: {name}")
            if stat.S_IMODE(info.st_mode) != 0o700:
                raise BottleRegistryError(f"Managed entry permissions must be exactly 0700: {name}")
            return _identity_of(info)
        finally:
            if root_fd is not None:
                os.close(root_fd)

    def verify(self, name: str) -> dict[str, Any]:
        """Revalidate a registered bottle's on-disk identity. Never repairs."""
        entry = self.get(name)
        identity = entry["identity"]
        root_fd = None
        try:
            root_fd = self._open_root()
            try:
                current = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                return {"name": name, "result": "MISSING"}
            except OSError as exc:
                raise BottleRegistryError(f"Could not examine bottle entry ({exc.errno})") from exc
            if stat.S_ISLNK(current.st_mode):
                return {"name": name, "result": "SYMLINK"}
            if not stat.S_ISDIR(current.st_mode):
                return {"name": name, "result": "NOT_A_DIRECTORY"}
            if not _identity_matches(current, identity):
                return {"name": name, "result": "IDENTITY_CHANGED"}
            if stat.S_IMODE(current.st_mode) != 0o700:
                return {"name": name, "result": "MODE_CHANGED"}
            return {"name": name, "result": "OK", "identity": _identity_of(current)}
        finally:
            if root_fd is not None:
                os.close(root_fd)

    def orphans(self) -> dict[str, Any]:
        """List disagreements between the registry and the managed root."""
        bottles = self.read()
        root_fd = None
        try:
            root_fd = self._open_root()
        except FileNotFoundError:
            return {
                "unregistered_on_disk": {},
                "unexpected_entries": {},
                "missing_on_disk": sorted(bottles),
                "identity_changed": [],
            }
        unregistered: dict[str, str] = {}
        unexpected: dict[str, str] = {}
        identity_changed: list[str] = []
        reserved = {REGISTRY_NAME, LOCK_NAME}
        for entry in sorted(os.listdir(root_fd)):
            if entry in reserved:
                continue
            try:
                info = os.stat(entry, dir_fd=root_fd, follow_symlinks=False)
            except OSError:
                unexpected[entry] = "inaccessible"
                continue
            if stat.S_ISLNK(info.st_mode):
                unexpected[entry] = "symlink"
                continue
            if entry in bottles:
                if not stat.S_ISDIR(info.st_mode):
                    unexpected[entry] = "not_a_directory"
                elif not _identity_matches(info, bottles[entry]["identity"]):
                    identity_changed.append(entry)
            elif stat.S_ISDIR(info.st_mode):
                unregistered[entry] = "directory"
            else:
                unexpected[entry] = "irregular_file"
        seen = set(unregistered) | set(unexpected) | set(identity_changed)
        missing = sorted(set(bottles) - seen)
        return {
            "unregistered_on_disk": unregistered,
            "unexpected_entries": unexpected,
            "missing_on_disk": missing,
            "identity_changed": sorted(identity_changed),
        }
