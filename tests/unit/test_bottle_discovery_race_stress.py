"""P1-A — Optional best-effort concurrent directory-replacement stress test.

Source: "Residual TOCTOU Risk Acceptance & Test Coverage Plan" v1.1
(docs/reviews/TOCTOU_RISK_ACCEPTANCE_2026-11-11.md), section "P1-A".

Classification: OPTIONAL / BEST-EFFORT. This test is non-deterministic by
nature; its outcome depends on scheduling and filesystem timing. It is:
- NOT a mathematical proof of absence of TOCTOU;
- NOT a mandatory merge gate;
- skipped unless WINE_AI_RACE_STRESS=1 so CI stays deterministic.

Acceptance criterion (from the plan): discovery must never silently succeed
pointing at an unexpected identity. Every emitted result path must, when
verified, either no longer exist (fail-closed: churned away mid-scan) or
hold an identity that was registered as a *complete* prefix before it
became scannable. Results carry no identity field by design (display
metadata only), so verification is post-hoc via os.stat on the reported
path — itself TOCTOU-prone, which is why this test is observational
evidence only, never proof.

Churn design: staging directories are built COMPLETE and REGISTERED in a
non-scanned area, then renamed into the scanned container root; the retired
prefix is registered before deletion. A partial (markers-missing) prefix is
therefore never scannable, so any result is either a registered identity or
a path that vanished mid-scan.

Controls honored: temporary directories only; no real user paths; no
~/.wine; no Wine execution; no sudo; no mounts; no sleep()-based security
evidence (thread yields are scheduling courtesies only).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from wine_ai.bottle_manager.discovery import discover_prefixes


def make_prefix(root: Path, name: str, arch: str = "win64") -> Path:
    path = root / name
    (path / "drive_c").mkdir(parents=True)
    (path / "system.reg").write_text(f"##arch - {arch}\n", encoding="ascii")
    (path / "user.reg").write_text("## WINE registry\n", encoding="ascii")
    (path / "userdef.reg").write_text("## WINE registry\n", encoding="ascii")
    return path


def identity_of(path: Path) -> tuple[int, int]:
    st = os.stat(path)
    return (st.st_dev, st.st_ino)


class BottleDiscoveryRaceStressTests(unittest.TestCase):
    """P1-A stress: concurrent rename/replacement churn vs. discovery."""

    CHURN_ITERATIONS = 200
    DISCOVERY_ROUNDS = 30

    def setUp(self):
        if os.environ.get("WINE_AI_RACE_STRESS") != "1":
            self.skipTest(
                "P1-A is optional/best-effort and non-deterministic; "
                "run explicitly with WINE_AI_RACE_STRESS=1"
            )
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.root = self.home / "containers" / "stress"
        self.root.mkdir(parents=True)
        self.build = self.home / "build"  # outside the scanned container root
        self.build.mkdir(parents=True)
        # Never let a real WINEPREFIX leak into the stress run.
        self._old_wp = os.environ.pop("WINEPREFIX", None)

    def tearDown(self):
        if self._old_wp is not None:
            os.environ["WINEPREFIX"] = self._old_wp
        self.temp.cleanup()

    def test_concurrent_replacement_never_yields_unexpected_identity(self):
        """Discovery reports only registered prefixes or paths gone mid-scan."""
        make_prefix(self.root, "prefix_a")
        make_prefix(self.root, "prefix_b")
        registered: set[tuple[int, int]] = {
            identity_of(self.root / "prefix_a"),
            identity_of(self.root / "prefix_b"),
        }
        reg_lock = threading.Lock()
        errors: list[str] = []
        stop = threading.Event()

        def register(path: Path) -> None:
            with reg_lock:
                registered.add(identity_of(path))

        def churn() -> None:
            counter = 0
            try:
                for _ in range(self.CHURN_ITERATIONS):
                    if stop.is_set():
                        return
                    counter += 1
                    staging = self.build / f"staging_{counter % 3}"
                    retired = self.root / f"retired_{counter % 3}"
                    try:
                        # os.replace cannot swap two non-empty directories, so
                        # the swap is a rename chain: prefix_a -> retired,
                        # staging -> prefix_a. Both retired and staging are
                        # registered identities; deleting retired mid-scan can
                        # only produce fail-closed ENOENT/BLOCKED outcomes.
                        if staging.exists():
                            continue  # a previous cycle still owns this slot
                        fresh = make_prefix(self.build, staging.name,
                                            arch="win32" if counter % 2 else "win64")
                        register(fresh)  # fully built, outside scan area
                        if (self.root / "prefix_a").exists():
                            os.rename(self.root / "prefix_a", retired)
                        os.rename(staging, self.root / "prefix_a")
                        if retired.exists():
                            shutil.rmtree(retired, ignore_errors=True)
                    except FileNotFoundError:
                        continue  # a concurrent cycle won this slot
                    except OSError as exc:
                        errors.append(f"churn OSError: {exc}")
            except Exception as exc:  # pragma: no cover — surfaced via errors
                errors.append(f"churn crashed: {exc!r}")
            finally:
                stop.set()

        thread = threading.Thread(target=churn, daemon=True)
        thread.start()

        unexpected: list[dict] = []
        runs = 0
        try:
            for _ in range(self.DISCOVERY_ROUNDS):
                results, _evidence = discover_prefixes(
                    home=self.home,
                    candidates=[self.root / "prefix_a", self.root / "prefix_b"],
                    container_roots=[self.root],
                )
                runs += 1
                with reg_lock:
                    snapshot = set(registered)
                for info in results:
                    path = Path(info.get("path", ""))
                    origin = info.get("origin", "")
                    # Results may only originate from the scanned root or the
                    # explicit candidates; never from the build area.
                    if "build" in path.parts:
                        unexpected.append({
                            "path": str(path), "origin": origin,
                            "reason": "emitted from non-scanned build area",
                        })
                        continue
                    try:
                        current = identity_of(path)
                    except FileNotFoundError:
                        continue  # fail-closed: churned away mid-scan
                    except OSError as exc:
                        errors.append(f"verify stat failed: {exc}")
                        continue
                    # Late registration tolerance: the identity may have been
                    # registered after this round's snapshot but before now.
                    with reg_lock:
                        known_now = current in registered
                    if not known_now and current not in snapshot:
                        unexpected.append({
                            "path": str(path), "origin": origin,
                            "identity": current,
                            "reason": "identity never registered as complete prefix",
                        })
        finally:
            stop.set()
            thread.join(timeout=10)

        self.assertEqual(errors, [], f"churn/verify errors: {errors}")
        self.assertEqual(
            unexpected, [],
            "Discovery silently reported prefixes that were never registered "
            "as complete (forbidden silent success)",
        )
        self.assertGreaterEqual(runs, 1, "discovery never ran under churn")


if __name__ == "__main__":
    unittest.main()
