"""Create Bottle entry point.

The write capability is deliberately disabled until a same-UID namespace
race can be eliminated by a stronger OS boundary. POSIX mkdirat() does not
return a descriptor for the newly created directory, so mkdirat()+openat()
cannot prove that the opened object is the object created if another process
with the same UID can rename entries between the calls. The old implementation
is retained below only as historical implementation context and is not
reachable through the public API.
from __future__ import annotations
import errno, fcntl, json, os, re, stat, time, uuid
from pathlib import Path
from typing import Any
from wine_ai.evidence import Evidence, EvidenceStatus

__all__ = ["create_bottle", "MANAGED_ROOT_PARTS", "NAME_PATTERN"]
MANAGED_ROOT_PARTS = (".local", "share", "wine-ai-adaptive", "prefixes")
AUDIT_DIR_PARTS = (".local", "share", "wine-ai-adaptive", "logs")
AUDIT_FILENAME = "create-bottle.jsonl"
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
DIR_MODE, FILE_MODE = 0o700, 0o600

class BottleCreateError(Exception):
    """A request was rejected or could not be completed safely."""

def _directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise BottleCreateError("Required no-follow directory flags are unavailable")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)

def _open_dir_path(path: Path | str) -> int:
    absolute = os.path.abspath(os.fspath(path)); flags = _directory_flags()
    fd = os.open("/", flags)
    try:
        for component in Path(absolute).parts[1:]:
            before = os.stat(component, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise BottleCreateError("Unsafe or non-directory home path component")
            nxt = os.open(component, flags, dir_fd=fd)
            try:
                after = os.fstat(nxt)
                if not stat.S_ISDIR(after.st_mode) or (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):
                    raise BottleCreateError("Home path component identity changed")
            except Exception:
                os.close(nxt); raise
            os.close(fd); fd = nxt
        return fd
    except Exception:
        os.close(fd); raise

def _open_child_directory(parent_fd: int, name: str, *, create: bool, private: bool) -> int:
    if not name or name in (".","..") or "/" in name:
        raise BottleCreateError("Invalid internal directory component")
    created = False
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if not create: raise
        try:
            os.mkdir(name, mode=DIR_MODE, dir_fd=parent_fd); created = True
        except FileExistsError:
            pass
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise BottleCreateError("Managed directory component is not a real directory")
    fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISDIR(opened.st_mode) or (before.st_dev,before.st_ino)!=(opened.st_dev,opened.st_ino):
            raise BottleCreateError("Managed directory identity changed during open")
        if private:
            if opened.st_uid != os.geteuid(): raise BottleCreateError("Managed directory is not user-owned")
            if created:
                os.fchmod(fd, DIR_MODE); opened = os.fstat(fd)
            if stat.S_IMODE(opened.st_mode) != DIR_MODE:
                raise BottleCreateError("Managed directory permissions must be exactly 0700")
        return fd
    except Exception:
        os.close(fd); raise

def _walk_private_path(home_fd: int, parts: tuple[str,...]) -> int:
    current = os.dup(home_fd)
    try:
        for part in parts:
            nxt = _open_child_directory(current, part, create=True,
                private=part in ("wine-ai-adaptive","prefixes","logs"))
            os.close(current); current = nxt
        return current
    except Exception:
        os.close(current); raise

def _open_audit_log(home_fd: int) -> int:
    logs_fd = _walk_private_path(home_fd, AUDIT_DIR_PARTS)
    try:
        flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | getattr(os,"O_CLOEXEC",0)
        created = False
        try:
            fd = os.open(AUDIT_FILENAME, flags|os.O_CREAT|os.O_EXCL, FILE_MODE, dir_fd=logs_fd)
            created = True
        except FileExistsError:
            fd = os.open(AUDIT_FILENAME, flags, dir_fd=logs_fd)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1):
                raise BottleCreateError("Audit log must be a singly-linked, user-owned regular file")
            if created:
                os.fchmod(fd, FILE_MODE); info = os.fstat(fd)
            if stat.S_IMODE(info.st_mode) != FILE_MODE:
                raise BottleCreateError("Audit log permissions must be exactly 0600")
            return fd
        except Exception:
            os.close(fd); raise
    finally:
        os.close(logs_fd)

def _append_audit(fd: int, *, operation_id: str, name: str|None, outcome: str, reason: str|None) -> None:
    record: dict[str,Any] = {
        "timestamp_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
        "operation_id":operation_id,"operation":"create_bottle","name":name,
        "relative_path":f"prefixes/{name}" if name else None,"outcome":outcome,"reason":reason}
    data=(json.dumps(record,ensure_ascii=True,separators=(",",":"))+"\n").encode()
    # Serialize cooperating writers and roll back a partial record on failure.
    fcntl.flock(fd, fcntl.LOCK_EX)
    start_size = os.fstat(fd).st_size
    try:
        remaining = memoryview(data)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError(errno.EIO, "audit-log write made no progress")
            remaining = remaining[written:]
        os.fsync(fd)
    except OSError:
        try:
            os.ftruncate(fd, start_size)
            os.fsync(fd)
        except OSError as rollback_error:
            raise OSError(errno.EIO, "audit append failed and partial-record rollback failed") from rollback_error
        raise
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)

def _validate_name(name: object) -> bool:
    return isinstance(name,str) and NAME_PATTERN.fullmatch(name) is not None

def _protected_root_identity(home_fd: int, root_fd: int) -> bool:
    try: protected=os.stat(".wine",dir_fd=home_fd,follow_symlinks=True)
    except FileNotFoundError: return False
    except OSError as exc: raise BottleCreateError("Could not verify protected ~/.wine identity") from exc
    if not stat.S_ISDIR(protected.st_mode): return False
    root=os.fstat(root_fd)
    return (protected.st_dev,protected.st_ino)==(root.st_dev,root.st_ino)

def _verify_created_directory(fd: int) -> os.stat_result:
    info=os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=DIR_MODE:
        raise BottleCreateError("Created directory failed type, ownership, or mode verification")
    return info

def _verify_target_entry(parent_fd: int, name: str, identity: tuple[int,int]) -> None:
    current=os.stat(name,dir_fd=parent_fd,follow_symlinks=False)
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev,current.st_ino)!=identity:
        raise BottleCreateError("Target directory entry no longer matches the created identity")

def _cleanup_created_directory(parent_fd: int,name: str,created_fd: int,identity: tuple[int,int]) -> bool:
    try:
        opened=os.fstat(created_fd); current=os.stat(name,dir_fd=parent_fd,follow_symlinks=False)
        if (opened.st_dev,opened.st_ino)!=identity or (current.st_dev,current.st_ino)!=identity or not stat.S_ISDIR(current.st_mode):
            return False
        os.rmdir(name,dir_fd=parent_fd); return True
    except OSError: return False

def _blocked(reason: str,name: str|None,operation_id: str,audit_fd: int|None=None):
    if audit_fd is not None:
        try: _append_audit(audit_fd,operation_id=operation_id,name=name,outcome="BLOCKED",reason=reason)
        except OSError: reason += "; audit logging also failed"
    return None,[Evidence("bottle_create",EvidenceStatus.BLOCKED,reason,"bottle_manager.create",
        {"operation_id":operation_id,"relative_path":f"prefixes/{name}" if name else None})]

def create_bottle(name: str) -> tuple[dict|None,list[Evidence]]:
    """Return BLOCKED until safe mkdir/open identity binding is available.

    No directories, audit files, locks, Wine processes, or registry entries
    are created by this public entry point. This is an intentional fail-closed
    feature gate, not a transient filesystem error.
    """
    operation_id = uuid.uuid4().hex
    valid_name = name if _validate_name(name) else None
    return None, [Evidence(
        "bottle_create",
        EvidenceStatus.BLOCKED,
        "Create Bottle is disabled: the same-UID mkdir/open namespace race "
        "cannot currently be closed safely",
        "bottle_manager.create",
        {
            "operation_id": operation_id,
            "name": valid_name,
            "feature_state": "DISABLED_PENDING_SECURITY_DESIGN",
            "reason": "mkdirat does not return an opened descriptor for the created inode",
        },
    )]
