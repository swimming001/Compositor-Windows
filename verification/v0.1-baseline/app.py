"""Independent Windows desktop implementation of Compositor's core workflow."""
from __future__ import annotations

import argparse
import copy
import functools
import json
import math
from pathlib import Path
import sys
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog, colorchooser

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

from engine import (BLEND_MODES, Document, History, Transform, brush, color_mask,
                    dimensions, export_image, filter_image, load_image,
                    load_project, new_id, render, save_project)

BG, PANEL, TEXT, ACCENT = "#191c23", "#232833", "#dbe2ed", "#67afff"


def guarded(function):
    @functools.wraps(function)
    def wrapped(self, *args, **kwargs):
        try:
            return function(self, *args, **kwargs)
        except Exception as error:
            self.status.set(str(error))
            messagebox.showerror("操作未完成", str(error), parent=self.root)
    return wrapped


class Editor:
    def __init__(self, root):
        self.root = root
        root.title("Compositor Windows · 核心移植预览版")
        root.geometry("1280x820")
        root.minsize(920, 620)
        root.configure(bg=BG)
        self.document, self.history = Document(), History()
        self.path, self.dirty = None, False
        self.tool = tk.StringVar(value="move")
        self.mask_target = tk.BooleanVar(value=False)
        self.radius = tk.IntVar(value=16)
        self.color = "#ffffff"
        self.zoom, self.offset = 1, (0, 0)
        self.fit_mode = True
        self.press_point = self.previous_point = None
        self.crop_box = None
        self.refresh_job = None
        self.photo = None
        self.status = tk.StringVar(value="新建画布或导入图片开始。PSD/PSB 以保存的合成预览导入。")
        self.inspector = {name: tk.StringVar() for name in ("x", "y", "width", "height", "rotation", "opacity")}
        self.blend = tk.StringVar(value="Normal")
        self._theme()
        self._menu()
        self._layout()
        self._bindings()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self.callback_error
        self.refresh()

    def _theme(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=PANEL, foreground=TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background=PANEL)
        style.configure("TLabel", background=PANEL)
        style.configure("TButton", padding=(10, 7), background="#333b49", foreground=TEXT, borderwidth=0)
        style.map("TButton", background=[("active", "#465773")])
        style.configure("TRadiobutton", padding=(5, 7))
        style.configure("TCheckbutton", padding=5)
        style.configure("TEntry", fieldbackground="#151922", foreground=TEXT, insertcolor=TEXT)
        style.configure("TSpinbox", fieldbackground="#151922", foreground=TEXT, arrowcolor=TEXT)
        style.configure("TCombobox", fieldbackground="#151922", foreground=TEXT, arrowcolor=TEXT)
        style.map("TCombobox", fieldbackground=[("readonly", "#151922")], foreground=[("readonly", TEXT)])
        style.configure("Treeview", background="#1b202a", fieldbackground="#1b202a", foreground=TEXT, rowheight=30, borderwidth=0)
        style.map("Treeview", background=[("selected", "#284d77")])
        style.configure("Treeview.Heading", background=PANEL, foreground=TEXT)
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)

    def _menu(self):
        menu = tk.Menu(self.root, bg=PANEL, fg=TEXT, tearoff=False)
        self.root.config(menu=menu)
        def section(title, entries):
            sub = tk.Menu(menu, tearoff=False, bg=PANEL, fg=TEXT)
            menu.add_cascade(label=title, menu=sub)
            for label, callback, shortcut in entries:
                if label is None: sub.add_separator()
                else: sub.add_command(label=label, command=callback, accelerator=shortcut)
        section("文件", [("新建画布…", self.new_document, "Ctrl+N"), ("打开 .comp 工程…", self.open_document, "Ctrl+O"),
                         ("导入图片 / PSD…", self.import_images, "Ctrl+I"), (None, None, ""),
                         ("保存工程", self.save, "Ctrl+S"), ("另存为…", lambda: self.save(True), "Ctrl+Shift+S"),
                         ("导出 PNG / JPEG…", self.export, "Ctrl+Shift+E"), (None, None, ""), ("退出", self.close, "")])
        section("编辑", [("撤销", self.undo, "Ctrl+Z"), ("重做", self.redo, "Ctrl+Shift+Z"),
                         ("复制图层", self.duplicate_layer, "Ctrl+J"), ("删除图层", self.delete_layer, "Delete")])
        section("图层", [("新建透明图层", self.blank_layer, ""), ("添加文字（栅格图层）…", self.add_text, "T"),
                         ("重命名…", self.rename_layer, ""), ("显示 / 隐藏", self.toggle_visible, ""),
                         ("添加白色蒙版", self.add_mask, ""), ("启用 / 禁用蒙版", self.toggle_mask, ""),
                         ("反相蒙版", self.invert_mask, ""), ("水平翻转", lambda: self.flip("x"), ""),
                         ("垂直翻转", lambda: self.flip("y"), "")])
        section("图像", [("亮度…", lambda: self.adjust("brightness"), ""), ("对比度…", lambda: self.adjust("contrast"), ""),
                         ("饱和度…", lambda: self.adjust("saturation"), ""), ("高斯模糊…", lambda: self.adjust("blur"), ""),
                         ("自动对比度", lambda: self.adjust("autocontrast"), ""), ("灰度", lambda: self.adjust("grayscale"), ""),
                         ("反相", lambda: self.adjust("invert"), ""), (None, None, ""),
                         ("按颜色抠图…", self.remove_color, ""), ("按矩形裁剪画布", self.apply_crop, "Enter")])
        section("视图", [("适合窗口", self.fit, "Ctrl+0"), ("实际像素", lambda: self.set_zoom(1), "Ctrl+1"),
                         ("放大", lambda: self.set_zoom(self.zoom*1.25), "+"), ("缩小", lambda: self.set_zoom(self.zoom/1.25), "-")])
        section("帮助", [("使用说明", self.help, "F1"), ("关于移植版", self.about, "")])

    def _layout(self):
        top = ttk.Frame(self.root, padding=(14, 8))
        top.pack(fill="x")
        ttk.Label(top, text="COMPOSITOR", font=("Segoe UI", 16, "bold"), foreground=ACCENT).pack(side="left", padx=(0, 18))
        for name, action in (("新建", self.new_document), ("导入", self.import_images), ("保存", self.save), ("导出", self.export)):
            ttk.Button(top, text=name, command=action).pack(side="left", padx=3)
        ttk.Label(top, text="Windows 核心移植预览版 0.1", foreground="#97a4b9").pack(side="right")
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, width=96, padding=(7, 12))
        left.pack(side="left", fill="y")
        for label, key in (("移动  V", "move"), ("画笔  B", "brush"), ("橡皮  E", "erase"), ("裁剪  C", "crop")):
            ttk.Radiobutton(left, text=label, value=key, variable=self.tool).pack(fill="x", pady=4)
        ttk.Button(left, text="文字  T", command=self.add_text).pack(fill="x", pady=(10, 5))
        ttk.Button(left, text="颜色", command=self.choose_color).pack(fill="x", pady=5)
        self.color_swatch = tk.Label(left, bg=self.color, height=1, width=5)
        self.color_swatch.pack(fill="x", padx=8, pady=(0, 16))
        ttk.Label(left, text="笔刷半径").pack()
        ttk.Spinbox(left, from_=1, to=250, width=6, textvariable=self.radius).pack(pady=5)
        ttk.Checkbutton(left, text="编辑蒙版", variable=self.mask_target).pack(fill="x", pady=10)
        right = ttk.Frame(body, width=270, padding=12)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        ttk.Label(right, text="图层", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", pady=(0, 10))
        self.tree = ttk.Treeview(right, columns=("visible",), displaycolumns=("visible",), show="tree", selectmode="browse", height=10)
        self.tree.column("#0", width=195)
        self.tree.column("visible", width=30, stretch=False)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.select_layer)
        self.tree.bind("<Double-1>", lambda event: self.toggle_visible())
        row = ttk.Frame(right)
        row.pack(fill="x", pady=8)
        for label, callback in (("＋", self.blank_layer), ("↑", lambda: self.reorder(1)), ("↓", lambda: self.reorder(-1)), ("删", self.delete_layer)):
            ttk.Button(row, text=label, command=callback, width=3).pack(side="left", padx=2)
        ttk.Separator(right).pack(fill="x", pady=8)
        ttk.Label(right, text="当前图层属性", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        form = ttk.Frame(right)
        form.pack(fill="x", pady=8)
        for index, (key, label) in enumerate((("x", "X"), ("y", "Y"), ("width", "宽"), ("height", "高"), ("rotation", "角度"), ("opacity", "透明度 %"))):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", pady=3)
            ttk.Entry(form, textvariable=self.inspector[key], width=15).grid(row=index, column=1, sticky="ew", pady=3, padx=(15, 0))
        ttk.Combobox(right, textvariable=self.blend, values=BLEND_MODES, state="readonly").pack(fill="x", pady=4)
        ttk.Button(right, text="应用图层属性", command=self.apply_inspector).pack(fill="x", pady=5)
        ttk.Label(right, text="双击图层：显示 / 隐藏\n滚轮：缩放 · 右键拖动：平移\n蒙版：画笔显示，橡皮隐藏", foreground="#96a5b9", justify="left").pack(anchor="w", pady=10)
        self.canvas = tk.Canvas(body, bg="#12151b", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda event: self.schedule_render())
        self.canvas.bind("<ButtonPress-1>", self.pointer_down)
        self.canvas.bind("<B1-Motion>", self.pointer_move)
        self.canvas.bind("<ButtonRelease-1>", self.pointer_up)
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<ButtonPress-3>", self.pan_down)
        self.canvas.bind("<B3-Motion>", self.pan_move)
        footer = ttk.Frame(self.root, padding=(12, 5))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status, anchor="w").pack(side="left", fill="x", expand=True)
        self.info = tk.StringVar()
        ttk.Label(footer, textvariable=self.info, foreground=ACCENT).pack(side="right")

    def _bindings(self):
        actions = {"<Control-n>": self.new_document, "<Control-o>": self.open_document,
                   "<Control-i>": self.import_images, "<Control-s>": self.save,
                   "<Control-Shift-S>": lambda: self.save(True), "<Control-Shift-E>": self.export,
                   "<Control-z>": self.undo, "<Control-Shift-Z>": self.redo,
                   "<Control-j>": self.duplicate_layer, "<Control-Key-0>": self.fit,
                   "<Control-Key-1>": lambda: self.set_zoom(1), "<F1>": self.help}
        for event, action in actions.items():
            self.root.bind(event, lambda ev, callback=action: self.shortcut(callback, ev))
        for key, tool in (("v", "move"), ("b", "brush"), ("e", "erase"), ("c", "crop")):
            self.root.bind(key, lambda ev, value=tool: self.shortcut(lambda: self.tool.set(value), ev))
        self.root.bind("t", lambda ev: self.shortcut(self.add_text, ev))
        self.root.bind("<Delete>", lambda ev: self.shortcut(self.delete_layer, ev))
        self.root.bind("<Return>", lambda ev: self.shortcut(self.apply_crop, ev))
        self.root.bind("<Escape>", lambda ev: self.cancel_crop())

    def shortcut(self, callback, event):
        if isinstance(self.root.focus_get(), (tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox, tk.Text)):
            return
        callback()
        return "break"

    def checkpoint(self):
        self.history.push(self.document)
        self.dirty = True

    def changed(self, text="已更新。"):
        self.dirty = True
        self.status.set(text)
        self.refresh()

    def refresh(self):
        self.root.title(f"{'* ' if self.dirty else ''}{self.path.stem if self.path else '未命名'} · Compositor Windows")
        selection = self.document.active_id
        self.tree.delete(*self.tree.get_children())
        lookup = {layer.id: layer for layer in self.document.layers}
        for layer in reversed(self.document.layers):
            depth, parent = 0, layer.parent_id
            while parent:
                depth += 1
                parent = lookup[parent].parent_id
            name = "  "*depth + ("▣ " if layer.is_group else "") + layer.name + (" [蒙版]" if layer.mask else "")
            self.tree.insert("", "end", iid=layer.id, text=name, values=("●" if layer.visible else "○",))
        if selection in lookup:
            self.tree.selection_set(selection)
            self.tree.see(selection)
        self.update_inspector()
        self.schedule_render()

    def select_layer(self, event=None):
        selected = self.tree.selection()
        if selected:
            self.document.active_id = selected[0]
        self.update_inspector()
        self.schedule_render()

    def update_inspector(self):
        layer = self.document.active
        values = [layer.transform.x, layer.transform.y, layer.transform.width, layer.transform.height,
                  layer.transform.rotation, layer.opacity*100] if layer else [0, 0, 0, 0, 0, 100]
        for key, value in zip(self.inspector, values): self.inspector[key].set(f"{value:g}")
        self.blend.set(layer.blend if layer else "Normal")

    def schedule_render(self):
        if self.refresh_job: self.root.after_cancel(self.refresh_job)
        self.refresh_job = self.root.after(25, self.draw)

    @guarded
    def draw(self):
        self.refresh_job = None
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        if self.fit_mode:
            self.zoom = max(.02, min((width-60)/self.document.width, (height-60)/self.document.height, 2))
            self.offset = ((width-self.document.width*self.zoom)/2, (height-self.document.height*self.zoom)/2)
        # Preview rendering is bounded independently of export resolution.
        raster = render(self.document, (min(1400, max(1, round(self.document.width*self.zoom))),
                                        min(1000, max(1, round(self.document.height*self.zoom)))))
        y, x = np.indices((raster.height, raster.width))
        gray = np.uint8(np.where((x//16+y//16) % 2, 80, 104))
        checker = Image.fromarray(np.stack((gray, gray, gray, np.full_like(gray, 255)), axis=2))
        visible = Image.alpha_composite(checker, raster).convert("RGB")
        screen_size = (max(1, round(self.document.width*self.zoom)), max(1, round(self.document.height*self.zoom)))
        # Avoid unbounded zoom bitmaps. Draw the visible canvas region only.
        ox, oy = self.offset
        left, top = max(0, -ox), max(0, -oy)
        right, bottom = min(screen_size[0], width-ox), min(screen_size[1], height-oy)
        self.canvas.delete("all")
        if right > left and bottom > top:
            sx, sy = visible.width/screen_size[0], visible.height/screen_size[1]
            cropped = visible.transform((max(1, round(right-left)), max(1, round(bottom-top))), Image.Transform.AFFINE,
                                        (sx, 0, left*sx, 0, sy, top*sy), resample=Image.Resampling.BILINEAR)
            self.photo = ImageTk.PhotoImage(cropped)
            self.canvas.create_image(ox+left, oy+top, image=self.photo, anchor="nw")
        self.canvas.create_rectangle(ox, oy, ox+screen_size[0], oy+screen_size[1], outline="#68748b")
        layer = self.document.active
        if layer and not layer.is_group:
            t, points = layer.transform, []
            for u, v in ((0, 0), (1, 0), (1, 1), (0, 1)):
                a = math.radians(t.rotation)
                dx, dy = (u-.5)*t.width, (v-.5)*t.height
                points.extend((ox+(t.x+t.width/2+dx*math.cos(a)-dy*math.sin(a))*self.zoom,
                               oy+(t.y+t.height/2+dx*math.sin(a)+dy*math.cos(a))*self.zoom))
            self.canvas.create_polygon(points, outline=ACCENT, fill="", width=1, dash=(4, 3))
        self.draw_crop()
        self.info.set(f"{self.document.width} × {self.document.height}   {self.zoom*100:.0f}%")

    def draw_crop(self):
        self.canvas.delete("crop")
        if self.crop_box:
            x1, y1, x2, y2 = self.crop_box
            ox, oy = self.offset
            self.canvas.create_rectangle(ox+x1*self.zoom, oy+y1*self.zoom, ox+x2*self.zoom, oy+y2*self.zoom,
                                         outline="#ffca75", width=2, dash=(6, 4), tags="crop")

    def doc_point(self, event):
        return ((event.x-self.offset[0])/self.zoom, (event.y-self.offset[1])/self.zoom)

    @guarded
    def pointer_down(self, event):
        self.canvas.focus_set()
        point = self.doc_point(event)
        self.press_point = self.previous_point = point
        if self.tool.get() == "crop":
            self.crop_box = (*point, *point)
            self.draw_crop()
            return
        layer = self.document.active
        if not layer or layer.is_group: self.press_point = None; return
        if self.tool.get() in ("brush", "erase") and layer.image is None:
            self.press_point = None
            raise ValueError("请选择有像素的图层。")
        self.checkpoint()
        if self.tool.get() == "move":
            self.drag_origin = (layer.transform.x, layer.transform.y)
        else:
            self.paint(point, point)
        self.schedule_render()

    def paint(self, start, end):
        brush(self.document.active, start, end, max(1, min(250, self.radius.get())), self.color,
              erase=self.tool.get() == "erase", on_mask=self.mask_target.get())

    @guarded
    def pointer_move(self, event):
        if self.press_point is None: return
        point = self.doc_point(event)
        if self.tool.get() == "crop":
            self.crop_box = (*self.press_point, *point)
            self.draw_crop()
            return
        layer = self.document.active
        if not layer or layer.is_group: return
        if self.tool.get() == "move":
            layer.transform.x = self.drag_origin[0]+point[0]-self.press_point[0]
            layer.transform.y = self.drag_origin[1]+point[1]-self.press_point[1]
        else: self.paint(self.previous_point, point)
        self.previous_point = point
        self.schedule_render()

    def pointer_up(self, event):
        if self.press_point is not None and self.tool.get() != "crop": self.changed()
        elif self.tool.get() == "crop": self.status.set("按 Enter 或选择“图像 > 按矩形裁剪画布”确认；Esc 取消。")
        self.press_point = None

    def pan_down(self, event):
        self.fit_mode = False
        self.pan_start = (event.x, event.y, *self.offset)

    def pan_move(self, event):
        x, y, ox, oy = self.pan_start
        self.offset = (ox+event.x-x, oy+event.y-y)
        self.schedule_render()

    def wheel(self, event):
        self.set_zoom(self.zoom*(1.15 if event.delta > 0 else 1/1.15), (event.x, event.y))

    def set_zoom(self, value, anchor=None):
        value = max(.02, min(8, value))
        anchor = anchor or (self.canvas.winfo_width()/2, self.canvas.winfo_height()/2)
        x, y = anchor
        self.offset = (x-(x-self.offset[0])*value/self.zoom, y-(y-self.offset[1])*value/self.zoom)
        self.zoom, self.fit_mode = value, False
        self.schedule_render()

    def fit(self): self.fit_mode = True; self.schedule_render()
    def cancel_crop(self): self.crop_box = None; self.draw_crop()

    def confirm_discard(self):
        if not self.dirty: return True
        answer = messagebox.askyesnocancel("未保存的修改", "是否先保存工程？", parent=self.root)
        if answer is None: return False
        return self.save() if answer else True

    @guarded
    def new_document(self):
        width = simpledialog.askinteger("新建画布", "宽度（像素）", initialvalue=1280, minvalue=1, maxvalue=12000, parent=self.root)
        if width is None: return
        height = simpledialog.askinteger("新建画布", "高度（像素）", initialvalue=800, minvalue=1, maxvalue=12000, parent=self.root)
        if height is None: return
        dimensions(width, height)
        if not self.confirm_discard(): return
        self.document, self.history = Document(width, height), History()
        self.path, self.dirty, self.crop_box = None, False, None
        self.fit_mode = True
        self.refresh()

    @guarded
    def open_document(self):
        path = filedialog.askdirectory(title="选择 .comp 工程文件夹（内部含 manifest.json）", parent=self.root)
        if not path: return
        document = load_project(path)
        if not self.confirm_discard(): return
        self.document, self.history, self.path = document, History(), Path(path)
        self.dirty, self.crop_box, self.fit_mode = False, None, True
        self.status.set("已打开工程。Mac 文字/形状按保存的 PNG 显示；像素修改后转为栅格。")
        self.refresh()

    @guarded
    def import_images(self):
        paths = filedialog.askopenfilenames(title="导入图片", filetypes=[("图片 / PSD", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.gif *.psd *.psb"), ("全部文件", "*.*")], parent=self.root)
        if not paths: return
        imported = [(load_image(path), Path(path).stem) for path in paths]
        candidate = self.document.clone()
        for image, name in imported:
            layer = candidate.add(image, name)
            fit = min(1, candidate.width/image.width, candidate.height/image.height)
            layer.transform.width, layer.transform.height = image.width*fit, image.height*fit
            layer.transform.x, layer.transform.y = (candidate.width-layer.transform.width)/2, (candidate.height-layer.transform.height)/2
        self.checkpoint()
        self.document = candidate
        self.changed("图片已导入。PSD/PSB 使用保存的合成预览，未保留可编辑 PSD 图层。")

    @guarded
    def save(self, as_new=False):
        path = self.path
        if as_new or path is None:
            result = filedialog.asksaveasfilename(title="保存 .comp 工程（将创建文件夹）", defaultextension=".comp", filetypes=[("Compositor 工程", "*.comp")], parent=self.root)
            if not result: return False
            path = Path(result)
        save_project(self.document, path)
        self.path, self.dirty = Path(path), False
        self.status.set(f"工程已保存：{path}")
        self.refresh()
        return True

    @guarded
    def export(self):
        path = filedialog.asksaveasfilename(title="导出合成图片", defaultextension=".png", filetypes=[("PNG（保留透明）", "*.png"), ("JPEG（白色底）", "*.jpg")], parent=self.root)
        if not path: return
        self.root.config(cursor="watch")
        self.status.set("正在导出…")
        self.root.update_idletasks()
        try: export_image(self.document, path)
        finally: self.root.config(cursor="")
        self.status.set(f"图片已导出：{path}")

    def undo(self):
        if self.history.undo_stack: self.document = self.history.undo(self.document); self.changed("已撤销。")

    def redo(self):
        if self.history.redo_stack: self.document = self.history.redo(self.document); self.changed("已重做。")

    def require_layer(self, pixels=False):
        layer = self.document.active
        if layer is None or (pixels and (layer.is_group or layer.image is None)):
            raise ValueError("请先选择一个像素图层。" if pixels else "请先选择图层。")
        return layer

    @guarded
    def blank_layer(self):
        candidate = self.document.clone()
        candidate.add(Image.new("RGBA", (self.document.width, self.document.height)), "透明图层")
        self.checkpoint()
        self.document = candidate
        self.changed()

    @guarded
    def duplicate_layer(self):
        layer = self.require_layer(pixels=True)
        clone = copy.deepcopy(layer)
        if sum(l.image.width*l.image.height for l in self.document.layers if l.image) + clone.image.width*clone.image.height > 24_000_000:
            raise ValueError("复制后超过源像素预算。")
        clone.id, clone.name = new_id(), layer.name+" 副本"
        self.checkpoint()
        self.document.layers.insert(self.document.layers.index(layer)+1, clone)
        self.document.active_id = clone.id
        self.changed()

    @guarded
    def delete_layer(self):
        layer = self.require_layer()
        removed = {layer.id}
        for _ in range(65):
            more = {l.id for l in self.document.layers if l.parent_id in removed}
            if more <= removed: break
            removed |= more
        if layer.is_group and not messagebox.askyesno("删除图层组", "将同时删除组内图层，是否继续？", parent=self.root): return
        self.checkpoint()
        self.document.layers = [l for l in self.document.layers if l.id not in removed]
        for remaining in self.document.layers:
            if remaining.clipping_id in removed: remaining.clipping_id = None
        self.document.active_id = self.document.layers[-1].id if self.document.layers else None
        self.changed()

    @guarded
    def reorder(self, delta):
        layer = self.require_layer()
        i = self.document.layers.index(layer)
        j = i+delta
        if not 0 <= j < len(self.document.layers): return
        other = self.document.layers[j]
        if layer.is_group or other.is_group or layer.parent_id != other.parent_id:
            raise ValueError("预览版只支持同组内相邻像素图层排序，避免破坏 Mac 图层组结构。")
        self.checkpoint()
        self.document.layers[i], self.document.layers[j] = other, layer
        self.changed()

    @guarded
    def rename_layer(self):
        layer = self.require_layer()
        name = simpledialog.askstring("重命名图层", "图层名", initialvalue=layer.name, parent=self.root)
        if name:
            self.checkpoint(); layer.name = name; self.changed()

    @guarded
    def toggle_visible(self):
        layer = self.require_layer()
        self.checkpoint(); layer.visible = not layer.visible; self.changed()

    @guarded
    def apply_inspector(self):
        layer = self.require_layer()
        values = {k: float(v.get()) for k, v in self.inspector.items()}
        transform = Transform.from_dict({"origin": [values["x"], values["y"]], "size": [values["width"], values["height"]],
                                         "rotation": values["rotation"], "flipX": layer.transform.flip_x,
                                         "flipY": layer.transform.flip_y, "sampling": layer.transform.sampling})
        if not math.isfinite(values["opacity"]) or not 0 <= values["opacity"] <= 100: raise ValueError("透明度范围为 0–100。")
        if layer.is_group and self.blend.get() != "Normal": raise ValueError("图层组仅支持 Normal 模式。")
        self.checkpoint()
        layer.transform, layer.opacity, layer.blend = transform, values["opacity"]/100, self.blend.get()
        self.changed()

    @guarded
    def flip(self, axis):
        layer = self.require_layer(pixels=True)
        self.checkpoint()
        if axis == "x": layer.transform.flip_x = not layer.transform.flip_x
        else: layer.transform.flip_y = not layer.transform.flip_y
        self.changed()

    @guarded
    def add_mask(self):
        layer = self.require_layer(pixels=True)
        if layer.mask is None:
            self.checkpoint(); layer.mask = Image.new("L", layer.image.size, 255); self.changed()
        self.mask_target.set(True)

    @guarded
    def toggle_mask(self):
        layer = self.require_layer()
        if layer.mask is None: raise ValueError("当前图层没有蒙版。")
        self.checkpoint(); layer.mask_enabled = not layer.mask_enabled; self.changed()

    @guarded
    def invert_mask(self):
        from PIL import ImageOps
        layer = self.require_layer()
        if layer.mask is None: raise ValueError("当前图层没有蒙版。")
        self.checkpoint(); layer.mask = ImageOps.invert(layer.mask); self.changed()

    def choose_color(self):
        result = colorchooser.askcolor(self.color, title="选择画笔 / 文字颜色", parent=self.root)[1]
        if result: self.color = result; self.color_swatch.configure(bg=result)

    @guarded
    def add_text(self):
        text = simpledialog.askstring("添加文字", "输入文字（生成独立栅格图层）", parent=self.root)
        if not text: return
        size = simpledialog.askinteger("字号", "字号（像素）", initialvalue=64, minvalue=8, maxvalue=512, parent=self.root)
        if size is None: return
        font_path = Path("C:/Windows/Fonts/msyh.ttc")
        font = ImageFont.truetype(str(font_path), size) if font_path.exists() else ImageFont.truetype("arial.ttf", size)
        box = font.getbbox(text)
        dimensions(max(1, box[2]-box[0]+16), max(1, box[3]-box[1]+16))
        image = Image.new("RGBA", (max(1, box[2]-box[0]+16), max(1, box[3]-box[1]+16)))
        ImageDraw.Draw(image).text((8-box[0], 8-box[1]), text, font=font, fill=self.color)
        candidate = self.document.clone()
        layer = candidate.add(image, text[:30])
        layer.transform.x, layer.transform.y = (candidate.width-image.width)/2, (candidate.height-image.height)/2
        self.checkpoint(); self.document = candidate; self.changed("文字已添加为栅格图层，可移动、变换和重新添加。")

    @guarded
    def adjust(self, kind):
        layer = self.require_layer(pixels=True)
        value = 1
        if kind in ("brightness", "contrast", "saturation", "blur"):
            value = simpledialog.askfloat("图像调整", "半径（0–100）" if kind == "blur" else "系数（0–5，1 为原始值）", initialvalue=3 if kind == "blur" else 1,
                                          minvalue=0, maxvalue=100 if kind == "blur" else 5, parent=self.root)
            if value is None: return
        result = filter_image(layer.image, kind, value)
        self.checkpoint(); layer.image = result; layer.raster_changed(); self.changed("已调整像素，可撤销。")

    @guarded
    def remove_color(self):
        layer = self.require_layer(pixels=True)
        color = colorchooser.askcolor(title="选择要隐藏的背景颜色（适合纯色背景）", parent=self.root)[0]
        if not color: return
        tolerance = simpledialog.askfloat("按颜色抠图", "颜色容差（1–120）", initialvalue=25, minvalue=1, maxvalue=120, parent=self.root)
        if tolerance is None: return
        self.checkpoint(); color_mask(layer, color, tolerance); self.mask_target.set(True)
        self.changed("已按颜色生成蒙版。这是颜色抠图，可用画笔/橡皮修整。")

    @guarded
    def apply_crop(self):
        if self.crop_box is None: return
        x1, y1, x2, y2 = self.crop_box
        left, top = max(0, round(min(x1, x2))), max(0, round(min(y1, y2)))
        right, bottom = min(self.document.width, round(max(x1, x2))), min(self.document.height, round(max(y1, y2)))
        dimensions(right-left, bottom-top)
        self.checkpoint()
        self.document.width, self.document.height = right-left, bottom-top
        for layer in self.document.layers:
            layer.transform.x -= left; layer.transform.y -= top
            if layer.mask_placement: layer.mask_placement.x -= left; layer.mask_placement.y -= top
        for guide in self.document.guides:
            guide["position"] -= top if guide["axis"] == "horizontal" else left
        self.crop_box, self.fit_mode = None, True
        self.changed("画布已裁剪，图层源像素仍保留。")

    def help(self):
        messagebox.showinfo("快速使用", "1. 文件 > 新建画布，导入图片\n2. V 移动；右侧输入尺寸/角度并应用\n3. B 画笔、E 橡皮；勾选编辑蒙版以修整蒙版\n4. 图像 > 按颜色抠图适合纯色背景\n5. T 添加栅格文字；C 拖选裁剪范围，Enter 确认\n6. Ctrl+S 保存 .comp；Ctrl+Shift+E 导出 PNG/JPEG\n\n完整说明见发行文件夹中的 README-中文.md。", parent=self.root)

    def about(self):
        messagebox.showinfo("关于", "Compositor Windows 0.1\n独立的 Windows 核心移植预览版，非上游官方发行版。\n基于 Robbie Tilton / Wonder Assembly 的 MIT 开源工程设计。\n\n暂未移植：AI 主体分割、RAW、调整层、图层效果、完整 PSD 编辑、GPU 渲染。\nMac 文本/形状保留 PNG 及原元数据，像素修改后栅格化。", parent=self.root)

    def callback_error(self, kind, error, trace):
        traceback.print_exception(kind, error, trace)
        messagebox.showerror("界面错误", str(error), parent=self.root)

    def close(self):
        if self.confirm_discard(): self.root.destroy()


def smoke_test(output, project=None):
    """Exercise an actual Tk window and import/save/export pipeline without dialogs."""
    folder = Path(output)
    folder.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    editor = Editor(root)
    if project and Path(project).suffix.lower() in (".psd", ".psb"):
        image = load_image(project)
        editor.document = Document(*image.size)
        editor.document.add(image, "PSD saved preview")
    elif project: editor.document = load_project(project)
    else:
        editor.document = Document(160, 100)
        editor.document.add(Image.new("RGBA", (160, 100), "#345577"), "背景")
        layer = editor.document.add(Image.new("RGBA", (50, 40), "#ee8855"), "前景")
        layer.transform.x, layer.transform.y, layer.opacity = 50, 30, .7
    editor.refresh()
    root.update()
    editor.draw()
    root.update()
    save_project(editor.document, folder/"smoke.comp")
    export_image(editor.document, folder/"smoke.png")
    # Capture only this application's window, when Windows permits a desktop capture.
    try:
        from PIL import ImageGrab
        import ctypes
        handle = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        ImageGrab.grab(window=handle).save(folder/"window.png")
    except OSError:
        pass
    (folder/"smoke-result.json").write_text(json.dumps({"ok": True, "size": [editor.document.width, editor.document.height],
                                                       "layers": len(editor.document.layers), "frozen": bool(getattr(sys, "frozen", False))}), encoding="utf-8")
    root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project", nargs="?")
    parser.add_argument("--smoke-test", metavar="OUTPUT_FOLDER")
    args = parser.parse_args()
    if args.smoke_test:
        smoke_test(args.smoke_test, args.project)
        return
    root = tk.Tk()
    editor = Editor(root)
    if args.project:
        try:
            editor.document = load_project(args.project)
            editor.path = Path(args.project)
            editor.refresh()
        except Exception as error: messagebox.showerror("无法打开工程", str(error), parent=root)
    root.mainloop()


if __name__ == "__main__":
    main()
