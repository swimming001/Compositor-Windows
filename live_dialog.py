"""Shared transactional Tk dialog lifecycle."""
import tkinter as tk
from tkinter import ttk


class LiveDialog:
    def __init__(self, editor, title):
        self.editor = editor
        self.window = tk.Toplevel(editor.root)
        self.window.title(title)
        self.window.configure(bg="#232833")
        self.window.geometry("470x430")
        self.window.transient(editor.root)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        self.body = ttk.Frame(self.window, padding=18)
        self.body.pack(fill="both", expand=True)
        self.note = tk.StringVar(value="调整时可在画布中查看效果，取消会还原。")
        self.job = None
        self.closed = False
        editor.modal_edit = self

    def buttons(self):
        ttk.Label(self.body, textvariable=self.note, wraplength=410, foreground="#a8b7ca").pack(fill="x", pady=8)
        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=6)
        ttk.Button(row, text="应用", command=self.accept).pack(side="right", padx=4)
        ttk.Button(row, text="取消", command=self.cancel).pack(side="right", padx=4)

    def schedule(self, *unused):
        if self.closed: return
        if self.job is None:
            self.job = self.window.after(100, self.preview)

    def preview(self):
        if self.job: self.window.after_cancel(self.job)
        self.job = None
        try:
            self.apply_preview()
            self.note.set("预览已更新。应用保留修改，取消还原。")
            return True
        except (ValueError, TypeError, KeyError, tk.TclError) as error:
            self.note.set(str(error))
            return False

    def finish(self):
        self.closed = True
        if self.job: self.window.after_cancel(self.job)
        self.editor.modal_edit = None
        # Variable traces keep a bound dialog method alive after the window closes.
        variables = list(vars(self).values())+list(getattr(self, "values", {}).values())
        for variable in variables:
            if isinstance(variable, tk.Variable):
                for mode, callback in variable.trace_info():
                    variable.trace_remove(mode, callback)
        self.window.grab_release()
        self.window.destroy()

    def accept(self):
        if self.job:
            self.window.after_cancel(self.job)
            self.job = None
        if self.preview():
            self.editor.finish_live_edit(True)
            self.finish()

    def cancel(self):
        self.editor.finish_live_edit(False)
        self.finish()
