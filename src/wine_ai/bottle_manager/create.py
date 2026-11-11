"""Fail-closed creation of empty, uninitialized managed Wine bottle directories."""
from __future__ import annotations
import errno, json, os, re, stat, time, uuid
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
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise BottleCreateError("Audit log must be a user-owned regular file")
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
    if os.write(fd,data)!=len(data): raise OSError(errno.EIO,"short audit-log write")
    os.fsync(fd)

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

def create_bottle(name: str, *, home: Path|None=None) -> tuple[dict|None,list[Evidence]]:
    """Create one empty directory under the managed root; never initialize Wine."""
    opid=uuid.uuid4().hex; valid=name if _validate_name(name) else None
    home_path=Path(home) if home is not None else Path.home()
    home_fd=audit_fd=root_fd=target_fd=None; identity=None
    try:
        home_fd=_open_dir_path(home_path)
        audit_fd=_open_audit_log(home_fd)
    except (OSError,BottleCreateError) as exc:
        for fd in (root_fd,audit_fd,home_fd):
            if fd is not None: os.close(fd)
        return _blocked(f"Audit facility unavailable; operation refused ({type(exc).__name__})",valid,opid)
    try:
        if valid is None:
            return _blocked("Invalid name: use 1-64 ASCII letters, digits, '-' or '_' and begin alphanumerically",None,opid,audit_fd)
        root_fd=_walk_private_path(home_fd,MANAGED_ROOT_PARTS)
        if _protected_root_identity(home_fd,root_fd):
            return _blocked("Managed prefixes root aliases protected ~/.wine identity",valid,opid,audit_fd)
        try: os.stat(valid,dir_fd=root_fd,follow_symlinks=False)
        except FileNotFoundError: pass
        except OSError as exc:
            return _blocked(f"Target examination failed ({errno.errorcode.get(exc.errno,'OSERROR')})",valid,opid,audit_fd)
        else: return _blocked("Target already exists; replacement is forbidden",valid,opid,audit_fd)
        try:
            os.mkdir(valid,mode=DIR_MODE,dir_fd=root_fd)
            target_fd=os.open(valid,_directory_flags(),dir_fd=root_fd)
            info=os.fstat(target_fd); identity=(info.st_dev,info.st_ino)
            os.fchmod(target_fd,DIR_MODE)
            info=_verify_created_directory(target_fd)
            if (info.st_dev,info.st_ino)!=identity: raise BottleCreateError("Created identity changed")
        except (OSError,BottleCreateError) as exc:
            if target_fd is not None and identity is not None:
                _cleanup_created_directory(root_fd,valid,target_fd,identity)
            return _blocked(f"Creation/postcondition failed ({type(exc).__name__})",valid,opid,audit_fd)
        try:
            _append_audit(audit_fd,operation_id=opid,name=valid,outcome="PROVEN",reason=None)
        except OSError:
            _cleanup_created_directory(root_fd,valid,target_fd,identity)
            return _blocked("Success audit record failed; cleanup attempted",valid,opid,audit_fd)
        return {"name":valid,"path":str(home_path.joinpath(*MANAGED_ROOT_PARTS,valid)),
            "origin":"created","protected":False,"architecture":None,
            "state":"EMPTY_UNINITIALIZED","created_at":time.time(),"operation_id":opid},[
            Evidence("bottle_create",EvidenceStatus.PROVEN,f"Empty uninitialized directory '{valid}' created",
                "bottle_manager.create",{"operation_id":opid,"relative_path":f"prefixes/{valid}",
                "mode":"0700","state":"EMPTY_UNINITIALIZED"})]
    except (OSError,BottleCreateError) as exc:
        return _blocked(f"Safe creation precondition failed ({type(exc).__name__})",valid,opid,audit_fd)
    finally:
        for fd in (target_fd,root_fd,audit_fd,home_fd):
            if fd is not None: os.close(fd)
