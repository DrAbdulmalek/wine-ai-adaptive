"""Unit tests for the bottle manager (no Wine required)."""

import json
import os
import stat

import pytest

from wine_ai.bottles import BottleError, BottleManager


class FakeWine:
    """Records wine invocations and pretends wineboot succeeded."""

    def __init__(self):
        self.calls = []

    def __call__(self, command, env=None, capture_output=True,
                 text=True, timeout=None, check=False):
        self.calls.append((command, env))
        import subprocess

        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def fake_wine(monkeypatch):
    fake = FakeWine()
    monkeypatch.setattr(
        "wine_ai.bottles.manager.subprocess.run", fake
    )
    return fake


def test_create_and_list_bottle(tmp_path, fake_wine):
    manager = BottleManager(root=tmp_path / "bottles")
    entry = manager.create_bottle("trados")
    assert fake_wine.calls, "wineboot should have been invoked"
    prefix = tmp_path / "bottles" / "trados"
    assert entry["path"] == str(prefix)
    assert manager.list_bottles() == {"trados": entry}


def test_duplicate_bottle_rejected(tmp_path, fake_wine):
    manager = BottleManager(root=tmp_path / "bottles")
    manager.create_bottle("memoq")
    with pytest.raises(BottleError):
        manager.create_bottle("memoq")


def test_invalid_bottle_name(tmp_path, fake_wine):
    manager = BottleManager(root=tmp_path / "bottles")
    for bad in ("../escape", "", "a/b", "x" * 100, 42):
        with pytest.raises(ValueError):
            manager.create_bottle(bad)


def test_unknown_bottle(tmp_path, fake_wine):
    manager = BottleManager(root=tmp_path / "bottles")
    with pytest.raises(BottleError):
        manager.get_bottle("missing")


def test_delete_bottle(tmp_path, fake_wine):
    manager = BottleManager(root=tmp_path / "bottles")
    manager.create_bottle("temp")
    manager.delete_bottle("temp")
    assert manager.list_bottles() == {}
    assert not (tmp_path / "bottles" / "temp").exists()


def test_registry_permissions(tmp_path, fake_wine):
    manager = BottleManager(root=tmp_path / "bottles")
    manager.create_bottle("secure")
    registry = tmp_path / "bottles" / "registry.json"
    mode = stat.S_IMODE(registry.lstat().st_mode)
    assert mode & 0o077 == 0
    data = json.loads(registry.read_text())
    assert "secure" in data["bottles"]


def test_root_directory_permissions(tmp_path, fake_wine):
    root = tmp_path / "bottles"
    BottleManager(root=root)
    mode = stat.S_IMODE(root.lstat().st_mode)
    assert mode & 0o077 == 0


def test_run_exe_rejects_symlink(tmp_path, fake_wine):
    manager = BottleManager(root=tmp_path / "bottles")
    manager.create_bottle("app")
    target = tmp_path / "real.exe"
    target.write_bytes(b"MZ")
    link = tmp_path / "link.exe"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlinks not supported on this platform")
    with pytest.raises(BottleError):
        manager.run_exe("app", link)


def test_run_exe_rejects_missing_file(tmp_path, fake_wine):
    manager = BottleManager(root=tmp_path / "bottles")
    manager.create_bottle("app")
    with pytest.raises(BottleError):
        manager.run_exe("app", tmp_path / "nope.exe")


def test_run_exe_passes_wineprefix(tmp_path, fake_wine):
    manager = BottleManager(root=tmp_path / "bottles")
    manager.create_bottle("app")
    exe = tmp_path / "setup.exe"
    exe.write_bytes(b"MZ")
    manager.run_exe("app", exe, args=["/S"])
    command, env = fake_wine.calls[-1]
    assert env["WINEPREFIX"] == str(tmp_path / "bottles" / "app")
    assert command[0] == "wine"
    assert command[-1] == "/S"


def test_run_command_validates_args(tmp_path, fake_wine):
    manager = BottleManager(root=tmp_path / "bottles")
    manager.create_bottle("app")
    with pytest.raises(ValueError):
        manager.run_command("app", ["ok", 42])
