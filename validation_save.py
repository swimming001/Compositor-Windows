"""Frozen-compatible checks of actual save dialogs, denied writes and retries."""
from pathlib import Path
import time
import tkinter as tk
from unittest.mock import patch
from PIL import Image
from app import Editor
from engine import Document,load_project
from save_dialog import SaveDestinationDialog


def verify(output):
    output=Path(output).absolute()
    root=tk.Tk();root.withdraw();editor=Editor(root)
    editor.document=Document(24,20);editor.document.add(Image.new("RGBA",(24,20),(80,120,160,255)),"原图")
    editor.output_directory=output;errors=[];expected=[]
    root.report_callback_exception=lambda *args:errors.append(str(args[1]))
    def wait():
        deadline=time.perf_counter()+15
        while editor.jobs.current and time.perf_counter()<deadline:root.update();time.sleep(.002)
        if editor.jobs.current or errors:raise RuntimeError("Save validation failed: "+str(errors))
    def drive(name,check_denied=False):
        def accept():
            dialog=next(w for w in root.winfo_children() if isinstance(w,SaveDestinationDialog))
            try:
                dialog.name.set(name)
                if check_denied:
                    with patch("save_dialog.check_directory",side_effect=PermissionError("validation denied")):
                        if dialog.validate():raise RuntimeError("Denied directory was accepted")
                    if "无法写入" not in dialog.note.get():raise RuntimeError("Permission feedback missing")
                dialog.directory.set(str(output));dialog.ok()
            except Exception as error:errors.append(str(error));dialog.cancel()
        root.after(30,accept)
    try:
        with patch("app.filedialog.asksaveasfilename",side_effect=RuntimeError("Win32 file save dialog must not receive project directory")),patch("save_dialog.messagebox.askyesno",return_value=True),patch("app.messagebox.showerror",side_effect=lambda *args,**kwargs:expected.append(str(args[1]))):
            drive("saved-from-dialog.comp",True);editor.save();wait()
            editor.document.active.name="第二次保存";editor.dirty=True
            drive("saved-from-dialog.comp");editor.save(True);wait()
            if editor.dirty or load_project(editor.path).active.name!="第二次保存":raise RuntimeError("Directory project replacement failed")
            editor.document.active.name="待恢复内容";editor.dirty=True;continued=[]
            with patch("app.save_project",side_effect=PermissionError("validation denied")):
                editor.save(after=lambda:continued.append(True))
            wait()
            if not editor.dirty or not editor.save_requires_location or continued or len(expected)!=1:raise RuntimeError("Denied save corrupted document state")
            if load_project(editor.path).active.name!="第二次保存":raise RuntimeError("Denied save changed existing project")
            drive("recovered-from-dialog.comp");editor.save(after=lambda:continued.append(True));wait()
            if editor.dirty or editor.save_requires_location or continued!=[True] or load_project(editor.path).active.name!="待恢复内容":raise RuntimeError("Save retry failed")
            drive("export-after-denied-save.png");editor.export();wait()
            with Image.open(output/"export-after-denied-save.png") as image:
                if image.size!=(24,20):raise RuntimeError("Export after failed save failed")
        if errors:raise RuntimeError(str(errors))
        return dict(save_dialog_verified=True,folder_projects_replace=True,save_permission_recovery=True,export_after_failed_save=True)
    finally:editor.shutdown();root.destroy()
