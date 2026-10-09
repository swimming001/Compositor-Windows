"""Regression checks for real destination dialogs and recovery after denied writes."""
import tempfile
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image
from app import Editor
from engine import Document,save_project,load_project
from save_dialog import (SaveDestinationDialog,destination,default_directory,check_directory,
                         PROJECT_FORMATS,IMAGE_FORMATS,PSD_FORMATS,TIFF_FORMATS)

ROOT=Path(__file__).resolve().parent


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/"verification");self.folder=Path(self.temp.name)
        self.root=tk.Tk();self.root.withdraw();self.editor=Editor(self.root)
        self.editor.document=Document(20,16);self.editor.document.add(Image.new("RGBA",(20,16),"red"),"原图")
        self.editor.output_directory=self.folder
        self.errors=patch("app.messagebox.showerror");self.error=self.errors.start()

    def tearDown(self):
        self.editor.shutdown();self.root.destroy();self.errors.stop();self.temp.cleanup()

    def wait(self):
        deadline=time.perf_counter()+10
        while self.editor.jobs.current and time.perf_counter()<deadline:self.root.update();time.sleep(.002)
        self.assertIsNone(self.editor.jobs.current)

    def drive(self,action):
        failures=[]
        def callback():
            dialog=next(w for w in self.root.winfo_children() if isinstance(w,SaveDestinationDialog))
            try:action(dialog)
            except BaseException as error:failures.append(error);dialog.cancel()
        self.root.after(30,callback)
        return failures

    def test_real_dialog_saves_new_and_existing_directory_project(self):
        for index in (1,2):
            self.editor.document.active.name=f"版本{index}";self.editor.dirty=True
            def accept(dialog):dialog.name.set("hello.comp");dialog.ok()
            failures=self.drive(accept)
            with patch("app.filedialog.asksaveasfilename",side_effect=AssertionError("Directory project sent to Win32 file dialog")),patch("save_dialog.messagebox.askyesno",return_value=True):
                self.editor.save(True)
            self.assertFalse(failures);self.wait();self.assertFalse(self.editor.dirty)
            self.assertEqual(load_project(self.folder/"hello.comp").active.name,f"版本{index}")
        self.assertFalse(self.error.called)

    def test_denied_directory_can_be_corrected_in_the_same_dialog(self):
        def accept(dialog):
            dialog.name.set("恢复.comp")
            with patch("save_dialog.check_directory",side_effect=PermissionError("denied")):
                self.assertFalse(dialog.validate())
            self.assertIn("无法写入",dialog.note.get());self.assertTrue(dialog.winfo_exists());self.assertIsNone(self.editor.jobs.current)
            dialog.directory.set(str(self.folder));dialog.ok()
        failures=self.drive(accept)
        dialog=SaveDestinationDialog(self.root,"保存",self.folder,"未命名.comp",PROJECT_FORMATS)
        self.assertFalse(failures);self.assertEqual(Path(dialog.result),self.folder/"恢复.comp")

    def test_cancel_real_dialog_preserves_unsaved_document(self):
        self.editor.dirty=True;before=self.editor.document
        failures=self.drive(lambda dialog:dialog.cancel());self.editor.save()
        self.assertFalse(failures);self.assertIs(before,self.editor.document);self.assertTrue(self.editor.dirty)
        self.assertIsNone(self.editor.jobs.current);self.assertIsNone(self.editor.path)

    def test_cancel_overwrite_keeps_existing_project_and_dialog_open(self):
        path=self.folder/"原稿.comp";save_project(self.editor.document,path)
        def accept(dialog):
            with patch("save_dialog.messagebox.askyesno",return_value=False):self.assertFalse(dialog.validate())
            self.assertTrue(dialog.winfo_exists());dialog.name.set("副本.comp");dialog.ok()
        failures=self.drive(accept);dialog=SaveDestinationDialog(self.root,"保存",self.folder,path.name,PROJECT_FORMATS)
        self.assertFalse(failures);self.assertEqual(Path(dialog.result),self.folder/"副本.comp")
        self.assertEqual(load_project(path).active.name,"原图")

    def test_real_export_dialog_selects_jpeg_and_writes_actual_jpeg(self):
        def accept(dialog):dialog.name.set("导出.png");dialog.format.set(IMAGE_FORMATS[1][0]);dialog.change_format();dialog.ok()
        failures=self.drive(accept);self.editor.export();self.wait()
        self.assertFalse(failures)
        with Image.open(self.folder/"导出.jpg") as image:self.assertEqual(image.format,"JPEG");self.assertEqual(image.size,(20,16))

    def test_permission_failure_keeps_original_and_retries_new_location(self):
        original=self.folder/"旧位置.comp";save_project(self.editor.document,original)
        self.editor.path=original;self.editor.document.active.name="未保存的新内容";self.editor.dirty=True;continued=[]
        with patch("app.save_project",side_effect=PermissionError("denied")):self.editor.save(after=lambda:continued.append(True))
        self.wait();self.assertTrue(self.editor.dirty);self.assertTrue(self.editor.save_requires_location);self.assertFalse(continued)
        self.assertEqual(load_project(original).active.name,"原图")
        target=self.folder/"新位置.comp"
        with patch("app.choose_save_destination",return_value=str(target)) as chooser:self.editor.save(after=lambda:continued.append(True))
        self.wait();chooser.assert_called_once();self.assertFalse(self.editor.save_requires_location);self.assertFalse(self.editor.dirty)
        self.assertEqual(continued,[True]);self.assertEqual(load_project(target).active.name,"未保存的新内容")
        with patch("app.choose_save_destination",return_value=str(self.folder/"after.png")):self.editor.export()
        self.wait();self.assertTrue((self.folder/"after.png").is_file())

    def test_export_denied_write_does_not_block_next_export(self):
        path=self.folder/"导出.png";self.editor.dirty=True
        with patch("app.choose_save_destination",return_value=str(path)),patch("app.export_image",side_effect=PermissionError("denied")):self.editor.export()
        self.wait();self.assertTrue(self.editor.dirty);self.assertFalse(path.exists())
        with patch("app.choose_save_destination",return_value=str(path)):self.editor.export()
        self.wait();self.assertTrue(path.is_file());self.assertTrue(self.editor.dirty)

    def test_busy_job_does_not_open_any_output_dialog(self):
        self.editor.start_job("计算",lambda:time.sleep(.1),(),lambda _:None)
        with patch("app.choose_save_destination") as chooser,patch("app.filedialog.askopenfilename") as source:
            self.editor.save();self.editor.export();self.editor.export_psd();self.editor.export_raw_tiff()
        chooser.assert_not_called();source.assert_not_called();self.wait()

    def test_psd_and_raw_tiff_share_new_destination_flow(self):
        psd=self.folder/"分层.psd"
        with patch("app.choose_save_destination",return_value=str(psd)) as chooser:self.editor.export_psd()
        self.wait();self.assertEqual(chooser.call_args.args[-1],PSD_FORMATS);self.assertTrue(psd.is_file())
        raw=next((ROOT/"verification/v0.3-fixtures").glob("*.KDC"));target=self.folder/"原始16.tif"
        with patch("app.filedialog.askopenfilename",return_value=str(raw)),patch("extended_dialogs.RawDialog",return_value=SimpleNamespace(result={})),patch("app.choose_save_destination",return_value=str(target)) as chooser:
            self.editor.export_raw_tiff()
        self.wait();self.assertEqual(chooser.call_args.args[-1],TIFF_FORMATS)
        with Image.open(target) as image:self.assertEqual(tuple(image.tag_v2[258]),(16,16,16))

    def test_project_name_does_not_overwrite_arbitrary_directory_or_file(self):
        occupied=self.folder/"用户资料.comp";occupied.mkdir();(occupied/"keep.txt").write_text("keep")
        with self.assertRaises(ValueError):destination(self.folder,occupied.name,PROJECT_FORMATS)
        occupied_file=self.folder/"file.comp";occupied_file.write_text("keep")
        with self.assertRaises(ValueError):destination(self.folder,occupied_file.name,PROJECT_FORMATS)
        self.assertEqual((occupied/"keep.txt").read_text(),"keep");self.assertEqual(occupied_file.read_text(),"keep")

    def test_windows_reserved_and_path_names_are_rejected(self):
        for name in ("CON","NUL.comp","../other","a:b","hello.","LPT1.png",""):
            with self.assertRaises(ValueError,msg=name):destination(self.folder,name,PROJECT_FORMATS)

    def test_default_falls_back_from_denied_directory_and_leaves_no_probe(self):
        blocked=self.folder/"blocked";blocked.mkdir()
        def check(path):
            if Path(path)==blocked:raise PermissionError("denied")
            check_directory(path)
        with patch("save_dialog.check_directory",side_effect=check):chosen=default_directory(blocked)
        self.assertNotEqual(chosen,blocked)
        self.assertEqual(default_directory(self.folder),self.folder)
        self.assertFalse(list(self.folder.glob(".compositor-write-check-*")))


if __name__=="__main__":unittest.main(verbosity=2)
