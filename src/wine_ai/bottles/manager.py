"""Wine bottle (WINEPREFIX) management with hardened storage.

Provides the BottleManager class, which creates, lists, and deletes
isolated Wine environments ("bottles") and runs Windows executables
inside them. Bottles live under a single root directory that is
verified on every open:

- the root and each bottle directory must be real directories,
  never symbolic links, owned by the current user, with mode 0700;
- the registry file is written atomically with owner-only (0600)
  permissions;
- executables are rejected if they are symbolic links or not
  regular files before Wine is ever invoked.

Documented limitations (NOT fully eliminated):

- path components above the root are not symlink-checked;
- check-then-use (TOCTOU) races cannot be fully removed from
  userspace; the pre-open validation narrows the windows;
- running Wine itself executes untrusted Windows code; only run
  installers and executables you trust.
"""

import json
import os
import re
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BOTTLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

REGISTRY_NAME = "registry.json"


class BottleError(Exception):
    """Raised for bottle management failures."""


def _validate_name(name):
    if not isinstance(name, str) or not BOTTLE_NAME.fullmatch(name):
        raise ValueError("Invalid bottle name")
    return name


def _check_dir(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise BottleError(f"Path is a symbolic link: {path}")
    if not stat.S_ISDIR(info.st_mode):
        raise BottleError(f"Path is not a directory: {path}")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise BottleError(f"Path is owned by a different user: {path}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        try:
            os.chmod(path, 0o700)
        except OSError as exc:
            raise BottleError(
                f"Could not restrict directory permissions: {path}"
            ) from exc
    return info


class BottleManager:
    """Manage isolated Wine prefixes under a single hardened root."""

    def __init__(self, root=None, wine_bin="wine"):
        if root is None:
            root = os.environ.get(
                "WINE_AI_BOTTLES_ROOT",
                "~/.local/share/wine-ai/bottles",
            )
        self.root = Path(root).expanduser()
        self.wine_bin = wine_bin
        self.registry_path = self.root / REGISTRY_NAME
        self._prepare_root()

    # -- filesystem hardening -------------------------------------------

    def _prepare_root(self):
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise BottleError(
                "Could not create bottles root directory"
            ) from exc
        _check_dir(self.root)

        if self.registry_path.exists():
            info = self.registry_path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise BottleError("Registry file is a symbolic link")
            if not stat.S_ISREG(info.st_mode):
                raise BottleError("Registry file is not a regular file")
            if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                raise BottleError(
                    "Registry file is owned by a different user"
                )
            try:
                os.chmod(self.registry_path, 0o600)
            except OSError as exc:
                raise BottleError(
                    "Could not restrict registry file permissions"
                ) from exc

    def _load_registry(self):
        if not self.registry_path.exists():
            return {"bottles": {}}
        try:
            data = json.loads(
                self.registry_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise BottleError("Registry file is corrupted") from exc
        if not isinstance(data, dict) or not isinstance(
            data.get("bottles"), dict
        ):
            raise BottleError("Registry file has an invalid structure")
        return data

    def _save_registry(self, registry):
        payload = json.dumps(registry, indent=2, ensure_ascii=False)
        tmp = self.registry_path.with_name(REGISTRY_NAME + ".tmp")
        flags = (
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(str(tmp), flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, self.registry_path)
        except OSError as exc:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise BottleError("Could not save registry") from exc
        try:
            os.chmod(self.registry_path, 0o600)
        except OSError as exc:
            raise BottleError(
                "Could not restrict registry file permissions"
            ) from exc

    # -- registry helpers -------------------------------------------------

    def _entry(self, name):
        registry = self._load_registry()
        entry = registry["bottles"].get(name)
        if entry is None:
            raise BottleError(f"Unknown bottle: {name}")
        return registry, entry

    # -- public API --------------------------------------------------------

    def bottle_path(self, name):
        _validate_name(name)
        return self.root / name

    def create_bottle(self, name, wine_bin=None):
        _validate_name(name)
        wine = wine_bin or self.wine_bin
        registry = self._load_registry()
        if name in registry["bottles"]:
            raise BottleError(f"Bottle already exists: {name}")

        path = self.bottle_path(name)
        try:
            path.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise BottleError(
                f"Bottle directory already exists on disk: {path}"
            ) from exc
        except OSError as exc:
            raise BottleError(
                "Could not create bottle directory"
            ) from exc

        try:
            self._run_wine(wine, path, ["wineboot", "-i"], timeout=120)
        except (BottleError, OSError) as exc:
            self._force_remove(path)
            raise BottleError(
                f"wineboot failed while creating bottle {name}: {exc}"
            ) from exc

        registry["bottles"][name] = {
            "path": str(path),
            "wine_bin": wine,
            "created_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
        }
        self._save_registry(registry)
        return dict(registry["bottles"][name])

    def list_bottles(self):
        registry = self._load_registry()
        return {
            name: dict(entry)
            for name, entry in registry["bottles"].items()
        }

    def get_bottle(self, name):
        _validate_name(name)
        _, entry = self._entry(name)
        return dict(entry)

    def delete_bottle(self, name):
        _validate_name(name)
        registry = self._load_registry()
        if name not in registry["bottles"]:
            raise BottleError(f"Unknown bottle: {name}")
        del registry["bottles"][name]
        self._save_registry(registry)
        self._force_remove(self.bottle_path(name))

    @staticmethod
    def _force_remove(path):
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode):
            path.unlink()
        elif stat.S_ISDIR(info.st_mode):
            shutil.rmtree(path)
        else:
            path.unlink()

    # -- execution ---------------------------------------------------------

    def _run_wine(self, wine_bin, prefix, args, timeout=None):
        env = os.environ.copy()
        env["WINEPREFIX"] = str(prefix)
        command = [wine_bin, *args]
        try:
            return subprocess.run(
                command,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise BottleError(
                f"Wine binary not found: {wine_bin}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise BottleError("Wine command timed out") from exc

    def run_exe(self, name, exe_path, args=None, timeout=None):
        _validate_name(name)
        _, entry = self._entry(name)

        exe = Path(exe_path).expanduser()
        try:
            info = exe.lstat()
        except FileNotFoundError as exc:
            raise BottleError(f"Executable not found: {exe}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise BottleError("Executable is a symbolic link")
        if not stat.S_ISREG(info.st_mode):
            raise BottleError("Executable is not a regular file")

        return self._run_wine(
            entry.get("wine_bin", self.wine_bin),
            self.bottle_path(name),
            [str(exe), *(args or [])],
            timeout=timeout,
        )

    def run_command(self, name, args, timeout=None):
        """Run arbitrary arguments (e.g. winetricks or msiexec) in a bottle."""
        _validate_name(name)
        _, entry = self._entry(name)
        if not isinstance(args, list) or not all(
            isinstance(item, str) for item in args
        ):
            raise ValueError("args must be a list of strings")
        return self._run_wine(
            entry.get("wine_bin", self.wine_bin),
            self.bottle_path(name),
            args,
            timeout=timeout,
        )
