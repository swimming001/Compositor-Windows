"""Save destinations for directory projects and files, without Win32 file probing."""
from pathlib import Path
import os
import re
import sys
import tempfile
import tkinter as tk
from tkinter import ttk,filedialog,messagebox,simpledialog

PROJECT_FORMATS=(("Compositor 工程", ".comp", (".comp",)),)
IMAGE_FORMATS=(("PNG（保留透明）", ".png", (".png",)),("JPEG（白色底）", ".jpg", (".jpg",".jpeg")))
PSD_FORMATS=(("Photoshop 分层工程", ".psd", (".psd",)),)
TIFF_FORMATS=(("16 位 RGB TIFF", ".tif", (".tif",".tiff")),)


def check_directory(directory):
    """Verify actual creation rights, rather than trusting os.access/ACL attributes."""
    directory=Path(directory)
    if not directory.is_dir():raise ValueError("保存位置必须是已有文件夹，请点击“浏览”选择。")
    fd,path=tempfile.mkstemp(prefix=".compositor-write-check-",dir=directory)
    try:os.close(fd)
    finally:Path(path).unlink()


def documents_directory():
    if sys.platform=="win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                value,_=winreg.QueryValueEx(key,"Personal")
            return Path(os.path.expandvars(value))
        except OSError:pass
    return Path.home()/"Documents"


def default_directory(preferred=None,project=None):
    # The portable folder also works when a preview launched with restricted
    # permissions cannot write to the user's home/Documents folder.
    portable=Path(sys.executable).parent if getattr(sys,"frozen",False) else Path(__file__).resolve().parent
    candidates=[preferred,Path(project).parent if project else None,portable,documents_directory(),Path.home()]
    for candidate in dict.fromkeys(Path(p) for p in candidates if p is not None):
        try:check_directory(candidate)
        except (OSError,ValueError):continue
        return candidate
    # Let the dialog explain the failure and offer a different folder.
    return portable


def destination(directory,name,formats,selected=0):
    directory=Path(directory).expanduser()
    name=name.strip()
    if not name or name in (".","..") or re.search(r'[<>:"/\\|?*\x00-\x1f]',name) or name.endswith(("."," ")):
        raise ValueError("请输入有效名称，不要包含路径、斜杠或 Windows 不支持的符号。")
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9]|CONIN\$|CONOUT\$",name.split(".")[0],re.I):
        raise ValueError("这个名称是 Windows 保留名称，请更换。")
    suffix=Path(name).suffix.lower()
    if not any(suffix in aliases for _,_,aliases in formats):name+=formats[selected][1]
    target=directory/name
    if formats==PROJECT_FORMATS:
        if target.is_symlink() or (target.exists() and (not target.is_dir() or not (target/"manifest.json").is_file())):
            raise ValueError("同名位置不是 Compositor 工程，不能覆盖。请使用其他名称。")
    elif target.is_dir() or target.is_symlink():
        raise ValueError("这个名称已被文件夹或链接占用，请使用其他名称。")
    check_directory(directory)
    return target


class SaveDestinationDialog(simpledialog.Dialog):
    def __init__(self,parent,title,initialdir,initialfile,formats):
        self.initialdir,self.initialfile,self.formats=initialdir,initialfile,formats
        self.result=None;self.chosen=None
        super().__init__(parent,title)

    def body(self,master):
        self.resizable(True,False)
        background=ttk.Style(self).lookup("TFrame","background") or self.parent.cget("background")
        self.configure(background=background)
        master.configure(background=background,padx=16,pady=12)
        master.columnconfigure(0,weight=1)
        ttk.Label(master,text="保存位置").grid(row=0,column=0,sticky="w")
        self.directory=tk.StringVar(value=str(self.initialdir))
        row=ttk.Frame(master);row.grid(row=1,column=0,sticky="ew",pady=(4,14));row.columnconfigure(0,weight=1)
        ttk.Entry(row,textvariable=self.directory,width=65).grid(row=0,column=0,sticky="ew")
        ttk.Button(row,text="浏览…",command=self.browse).grid(row=0,column=1,padx=(8,0))
        ttk.Label(master,text="工程名称" if self.formats==PROJECT_FORMATS else "文件名称").grid(row=2,column=0,sticky="w")
        self.name=tk.StringVar(value=self.initialfile)
        entry=ttk.Entry(master,textvariable=self.name);entry.grid(row=3,column=0,sticky="ew",pady=(4,14))
        self.format=tk.StringVar(value=self.formats[0][0])
        if len(self.formats)>1:
            ttk.Label(master,text="导出格式").grid(row=4,column=0,sticky="w")
            self.format_box=ttk.Combobox(master,textvariable=self.format,values=[f[0] for f in self.formats],state="readonly")
            self.format_box.grid(row=5,column=0,sticky="ew",pady=(4,14));self.format_box.bind("<<ComboboxSelected>>",self.change_format)
        note="工程将保存为 .comp 文件夹，原图和图层信息都保留。" if self.formats==PROJECT_FORMATS else "选择保存位置并填写名称，原工程保持不变。"
        self.note=tk.StringVar(value=note)
        ttk.Label(master,textvariable=self.note,wraplength=530).grid(row=6,column=0,sticky="w")
        entry.selection_range(0,tk.END)
        return entry

    def buttonbox(self):
        row=ttk.Frame(self,padding=(16,4,16,14))
        ttk.Button(row,text="取消",command=self.cancel).pack(side="right",padx=(8,0))
        ttk.Button(row,text="保存",command=self.ok).pack(side="right")
        row.pack(fill="x");self.bind("<Return>",self.ok);self.bind("<Escape>",self.cancel)

    def browse(self):
        current=Path(self.directory.get()).expanduser()
        selected=filedialog.askdirectory(title="选择保存位置",initialdir=str(current) if current.is_dir() else str(self.initialdir),mustexist=True,parent=self)
        if selected:self.directory.set(selected)

    def change_format(self,event=None):
        selected=[f[0] for f in self.formats].index(self.format.get())
        name=self.name.get();suffix=Path(name).suffix
        if any(suffix.lower() in f[2] for f in self.formats):name=name[:-len(suffix)]
        self.name.set(name+self.formats[selected][1])

    def validate(self):
        try:
            selected=[f[0] for f in self.formats].index(self.format.get())
            target=destination(self.directory.get(),self.name.get(),self.formats,selected)
            if target.exists() and not messagebox.askyesno("替换已有内容",f"{target.name} 已存在。是否替换？",parent=self):return False
            self.chosen=target
            return True
        except PermissionError:
            self.note.set("无法写入这个保存位置。请点击“浏览”选择其他文件夹；当前工程没有改变。")
        except (OSError,ValueError) as error:self.note.set(str(error))
        return False

    def apply(self):self.result=str(self.chosen)


def choose_save_destination(parent,title,initialdir,initialfile,formats):
    return SaveDestinationDialog(parent,title,initialdir,initialfile,formats).result
