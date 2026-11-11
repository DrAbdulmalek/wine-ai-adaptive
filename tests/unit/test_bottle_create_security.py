"""Security tests for Create Bottle. Every filesystem fixture is temporary."""
from __future__ import annotations
import errno, json, os, stat, tempfile, unittest
from pathlib import Path
from unittest import mock
from wine_ai.bottle_manager.create import AUDIT_DIR_PARTS,AUDIT_FILENAME,DIR_MODE,MANAGED_ROOT_PARTS,create_bottle
from wine_ai.evidence import EvidenceStatus

def app_root(home:Path)->Path:
    return home/".local"/"share"/"wine-ai-adaptive"
def managed_root(home:Path)->Path:
    return home.joinpath(*MANAGED_ROOT_PARTS)
def audit_file(home:Path)->Path:
    return home.joinpath(*AUDIT_DIR_PARTS,AUDIT_FILENAME)
def prepare_root(home:Path)->Path:
    app=app_root(home); app.mkdir(parents=True,mode=0o700); app.chmod(0o700)
    root=app/"prefixes"; root.mkdir(mode=0o700); return root

class CreateBottleSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.home=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def call_create(self,name):
        with mock.patch("wine_ai.bottle_manager.create.Path.home",return_value=self.home):
            return create_bottle(name)
    def audit_records(self):
        p=audit_file(self.home); self.assertTrue(p.is_file()); self.assertFalse(p.is_symlink())
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]

    def test_A01_rejects_invalid_names_and_audits_attempt(self):
        invalid=("",".","..","../escape","a/b",".hidden","a b","x$","a"*65,"line\nbreak")
        for name in invalid:
            with self.subTest(name=name):
                info,evidence=self.call_create(name); self.assertIsNone(info)
                self.assertEqual(evidence[0].status,EvidenceStatus.BLOCKED)
        records=self.audit_records(); self.assertEqual(len(records),len(invalid))
        self.assertTrue(all(x["outcome"]=="BLOCKED" and x["name"] is None for x in records))

    def test_A02_accepts_valid_boundary_names(self):
        for name in ("a","Bottle1","with-dash_2","A"*64):
            info,evidence=self.call_create(name); self.assertIsNotNone(info)
            self.assertEqual(info["state"],"EMPTY_UNINITIALIZED")
            self.assertEqual(evidence[0].status,EvidenceStatus.PROVEN)
            Path(info["path"]).rmdir()

    def test_A04_A05_public_api_cannot_escape_managed_root(self):
        outside=self.home/"outside"; outside.mkdir()
        sentinel=outside/"keep.txt"; sentinel.write_text("untouched")
        for name in ("../outside/escape","/tmp/absolute","x/../../outside"):
            info,evidence=self.call_create(name); self.assertIsNone(info)
            self.assertEqual(evidence[0].status,EvidenceStatus.BLOCKED)
        self.assertEqual(sentinel.read_text(),"untouched"); self.assertFalse((outside/"escape").exists())

    def test_B01_rejects_symlink_in_managed_root_component(self):
        (self.home/".local").mkdir(); outside=self.home/"outside"; outside.mkdir()
        (self.home/".local"/"share").symlink_to(outside,target_is_directory=True)
        info,evidence=self.call_create("blocked"); self.assertIsNone(info)
        self.assertEqual(evidence[0].status,EvidenceStatus.BLOCKED)
        self.assertFalse((outside/"wine-ai-adaptive").exists())

    def test_B02_rejects_existing_symlink_target_without_following(self):
        root=prepare_root(self.home); outside=self.home/"outside"; outside.mkdir()
        (root/"linked").symlink_to(outside,target_is_directory=True)
        info,_=self.call_create("linked"); self.assertIsNone(info)
        self.assertTrue((root/"linked").is_symlink()); self.assertTrue(outside.is_dir())

    def test_B03_B04_rejects_managed_root_alias_of_protected_wine(self):
        root=prepare_root(self.home)
        (self.home/".wine").symlink_to(root,target_is_directory=True)
        info,evidence=self.call_create("new"); self.assertIsNone(info)
        self.assertEqual(evidence[0].status,EvidenceStatus.BLOCKED)
        self.assertFalse((root/"new").exists())

    def test_C01_C02_C03_rejects_existing_directory_file_and_symlink(self):
        root=prepare_root(self.home); (root/"existing-dir").mkdir()
        (root/"existing-file").write_text("x")
        (root/"existing-link").symlink_to(self.home,target_is_directory=True)
        for name in ("existing-dir","existing-file","existing-link"):
            with self.subTest(name=name):
                info,evidence=self.call_create(name); self.assertIsNone(info)
                self.assertEqual(evidence[0].status,EvidenceStatus.BLOCKED)

    def test_D01_D02_D03_D04_creates_owned_plain_0700_directory(self):
        info,evidence=self.call_create("safe-bottle"); self.assertIsNotNone(info)
        target=Path(info["path"]); metadata=target.lstat()
        self.assertTrue(stat.S_ISDIR(metadata.st_mode)); self.assertFalse(stat.S_ISLNK(metadata.st_mode))
        self.assertEqual(metadata.st_uid,os.geteuid()); self.assertEqual(stat.S_IMODE(metadata.st_mode),DIR_MODE)
        self.assertEqual(info["state"],"EMPTY_UNINITIALIZED"); self.assertEqual(evidence[0].status,EvidenceStatus.PROVEN)
        self.assertEqual(list(target.iterdir()),[])

    def test_E01_postcondition_failure_cleans_only_created_empty_directory(self):
        with mock.patch("wine_ai.bottle_manager.create._verify_created_directory",side_effect=OSError("injected")):
            info,evidence=self.call_create("cleanup")
        self.assertIsNone(info); self.assertEqual(evidence[0].status,EvidenceStatus.BLOCKED)
        self.assertFalse((managed_root(self.home)/"cleanup").exists())

    def test_E02_rejection_creates_no_bottle_but_audits_attempt(self):
        info,_=self.call_create("../bad"); self.assertIsNone(info)
        self.assertFalse(managed_root(self.home).exists())
        self.assertEqual(self.audit_records()[0]["outcome"],"BLOCKED")

    def test_E03_filesystem_error_fails_closed(self):
        real_mkdir=os.mkdir
        def fail_target(name,mode=0o777,*,dir_fd=None):
            if name=="denied": raise PermissionError("injected")
            return real_mkdir(name,mode,dir_fd=dir_fd)
        with mock.patch("wine_ai.bottle_manager.create.os.mkdir",side_effect=fail_target):
            info,evidence=self.call_create("denied")
        self.assertIsNone(info); self.assertEqual(evidence[0].status,EvidenceStatus.BLOCKED)
        self.assertFalse((managed_root(self.home)/"denied").exists())

    def test_E03_resource_and_filesystem_errors_fail_closed(self):
        codes=(errno.EACCES,errno.EPERM,errno.EIO,errno.ENOSPC,errno.EMFILE,errno.ENFILE)
        real_mkdir=os.mkdir
        for code in codes:
            name=f"fault-{code}"
            def injected_mkdir(candidate,mode=0o777,*,dir_fd=None):
                if candidate==name: raise OSError(code,"injected filesystem failure")
                return real_mkdir(candidate,mode,dir_fd=dir_fd)
            with self.subTest(errno=code):
                with mock.patch("wine_ai.bottle_manager.create.os.mkdir",side_effect=injected_mkdir):
                    info,evidence=self.call_create(name)
                self.assertIsNone(info)
                self.assertEqual(evidence[0].status,EvidenceStatus.BLOCKED)
                self.assertFalse((managed_root(self.home)/name).exists())

    def test_rejects_preexisting_managed_root_with_permissive_mode(self):
        app=app_root(self.home); app.mkdir(parents=True,mode=0o700); app.chmod(0o755)
        info,evidence=self.call_create("unsafe-parent")
        self.assertIsNone(info)
        self.assertEqual(evidence[0].status,EvidenceStatus.BLOCKED)
        self.assertFalse((app/"prefixes"/"unsafe-parent").exists())

    def test_F01_F02_audit_records_success_and_failure(self):
        info,_=self.call_create("logged"); self.assertIsNotNone(info)
        failed,_=self.call_create("logged"); self.assertIsNone(failed)
        records=self.audit_records()
        self.assertEqual([x["outcome"] for x in records],["PROVEN","BLOCKED"])
        self.assertEqual(records[0]["relative_path"],"prefixes/logged")
        self.assertEqual(records[1]["reason"],"Target already exists; replacement is forbidden")
        Path(info["path"]).rmdir()

    def test_F03_audit_log_private_and_no_absolute_home_path(self):
        self.call_create("../invalid"); p=audit_file(self.home)
        self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600)
        self.assertNotIn(str(self.home),p.read_text(encoding="utf-8"))

    def test_audit_symlink_is_rejected_before_bottle_creation(self):
        app=app_root(self.home); app.mkdir(parents=True,mode=0o700); app.chmod(0o700)
        logs=app/"logs"; logs.mkdir(mode=0o700)
        outside=self.home/"outside-log"; outside.write_text("")
        (logs/AUDIT_FILENAME).symlink_to(outside)
        info,evidence=self.call_create("no-audit"); self.assertIsNone(info)
        self.assertEqual(evidence[0].status,EvidenceStatus.BLOCKED)
        self.assertFalse(managed_root(self.home).exists()); self.assertEqual(outside.read_text(),"")

    def test_audit_hardlink_is_rejected_before_bottle_creation(self):
        app=app_root(self.home); app.mkdir(parents=True,mode=0o700); app.chmod(0o700)
        logs=app/"logs"; logs.mkdir(mode=0o700)
        outside=self.home/"outside-log"; outside.write_text("keep")
        os.link(outside,logs/AUDIT_FILENAME)
        info,evidence=self.call_create("no-hardlink"); self.assertIsNone(info)
        self.assertEqual(evidence[0].status,EvidenceStatus.BLOCKED)
        self.assertFalse(managed_root(self.home).exists())
        self.assertEqual(outside.read_text(),"keep")

    def test_G01_no_external_process_or_wine_execution(self):
        with mock.patch("subprocess.Popen") as popen,mock.patch("os.system") as system:
            info,_=self.call_create("no-wine")
        self.assertIsNotNone(info); popen.assert_not_called(); system.assert_not_called()
        Path(info["path"]).rmdir()

    def test_G02_created_object_is_direct_child_of_managed_root(self):
        info,_=self.call_create("confined"); self.assertIsNotNone(info)
        target=Path(info["path"]); self.assertEqual(target.parent,managed_root(self.home)); target.rmdir()

    def test_F04_partial_audit_write_is_rolled_back_before_failure_record(self):
        real_write=os.write; calls={"count":0}
        def partial_then_fail(fd,data):
            calls["count"]+=1
            if calls["count"]==1:
                return real_write(fd,data[:max(1,len(data)//2)])
            if calls["count"]==2:
                raise OSError(errno.EIO,"injected mid-record failure")
            return real_write(fd,data)
        with mock.patch("wine_ai.bottle_manager.create.os.write",side_effect=partial_then_fail):
            info,evidence=self.call_create("audit-rollback")
        self.assertIsNone(info); self.assertEqual(evidence[0].status,EvidenceStatus.BLOCKED)
        records=self.audit_records()
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]["outcome"],"BLOCKED")
        self.assertFalse((managed_root(self.home)/"audit-rollback").exists())

    def test_G03_all_opened_and_duplicated_descriptors_are_closed(self):
        opened={}; closed={}
        real_open=os.open; real_dup=os.dup; real_close=os.close
        def track_open(*args,**kwargs):
            fd=real_open(*args,**kwargs); opened[fd]=opened.get(fd,0)+1; return fd
        def track_dup(fd):
            duplicate=real_dup(fd); opened[duplicate]=opened.get(duplicate,0)+1; return duplicate
        def track_close(fd):
            if fd in opened: closed[fd]=closed.get(fd,0)+1
            return real_close(fd)
        with mock.patch("wine_ai.bottle_manager.create.os.open",side_effect=track_open):
            with mock.patch("wine_ai.bottle_manager.create.os.dup",side_effect=track_dup):
                with mock.patch("wine_ai.bottle_manager.create.os.close",side_effect=track_close):
                    info,_=self.call_create("fd-check")
        self.assertIsNotNone(info)
        self.assertTrue(opened, "test did not observe any descriptors")
        self.assertEqual(opened,closed, f"descriptor open/close count mismatch: {opened} != {closed}")
        Path(info["path"]).rmdir()



    def test_H01_concurrent_same_name_creates_at_most_one_bottle(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)

        def worker():
            barrier.wait(timeout=5)
            return create_bottle("same-name")

        with mock.patch("wine_ai.bottle_manager.create.Path.home", return_value=self.home):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _index: worker(), range(2)))

        successes = [item for item, _evidence in results if item is not None]
        blocked = [evidence for item, evidence in results if item is None]
        self.assertEqual(len(successes), 1, results)
        self.assertEqual(len(blocked), 1, results)
        self.assertEqual(blocked[0][0].status, EvidenceStatus.BLOCKED)
        self.assertTrue((managed_root(self.home) / "same-name").is_dir())
        self.assertEqual(len(self.audit_records()), 2)
        (managed_root(self.home) / "same-name").rmdir()

    def test_H02_concurrent_audit_appends_remain_individual_jsonl_records(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        count = 8
        barrier = Barrier(count)

        def worker(index):
            barrier.wait(timeout=10)
            return create_bottle(f"parallel-{index}")

        with mock.patch("wine_ai.bottle_manager.create.Path.home", return_value=self.home):
            with ThreadPoolExecutor(max_workers=count) as pool:
                results = list(pool.map(worker, range(count)))

        self.assertTrue(all(item is not None for item, _evidence in results), results)
        records = self.audit_records()
        self.assertEqual(len(records), count)
        self.assertEqual({record["name"] for record in records},
                         {f"parallel-{index}" for index in range(count)})
        self.assertTrue(all(record["outcome"] == "PROVEN" for record in records))
        for item, _evidence in results:
            Path(item["path"]).rmdir()

    def test_H03_empty_created_bottle_is_not_discovered_as_wine_prefix(self):
        from wine_ai.bottle_manager.discovery import discover_prefixes
        info, _evidence = self.call_create("empty-not-prefix")
        self.assertIsNotNone(info)
        discovered, discovery_evidence = discover_prefixes(home=self.home)
        self.assertNotIn("empty-not-prefix", {item["name"] for item in discovered})
        self.assertTrue(any(
            item.status == EvidenceStatus.UNPROVEN
            and "missing markers" in item.message.lower()
            for item in discovery_evidence
        ), discovery_evidence)
        Path(info["path"]).rmdir()

    @unittest.expectedFailure
    def test_H04_same_uid_swap_between_mkdir_and_open_is_rejected(self):
        # Characterization test for the documented same-UID TOCTOU boundary.
        # It remains an expected failure until the implementation can bind
        # mkdir's result to the subsequently opened descriptor without
        # trusting the mutable directory entry.
        real_mkdir = os.mkdir
        swapped = {"done": False}

        def swap_after_target_mkdir(name, mode=0o777, *, dir_fd=None):
            result = real_mkdir(name, mode, dir_fd=dir_fd)
            if name == "race-swap" and not swapped["done"]:
                swapped["done"] = True
                os.rename(name, "displaced-race-swap",
                          src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                real_mkdir(name, mode, dir_fd=dir_fd)
            return result

        with mock.patch("wine_ai.bottle_manager.create.Path.home", return_value=self.home):
            with mock.patch("wine_ai.bottle_manager.create.os.mkdir",
                            side_effect=swap_after_target_mkdir):
                info, _evidence = create_bottle("race-swap")
        try:
            # Current code accepts the replacement; expectedFailure keeps this
            # known residual visible instead of claiming the race is defeated.
            self.assertIsNone(info)
        finally:
            root = managed_root(self.home)
            for name in ("race-swap", "displaced-race-swap"):
                target = root / name
                if target.is_dir() and not target.is_symlink():
                    target.rmdir()

if __name__ == "__main__":
    unittest.main()
