"""Independent Windows desktop implementation of Compositor's core workflow."""
from __future__ import annotations

import argparse
import copy
import functools
import gc
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog, colorchooser

from PIL import Image, ImageTk

from engine import (BLEND_MODES, Document, History, Transform, brush, color_mask,
                    dimensions, export_image, filter_image, load_image,
                    load_project, new_id, render, save_project, Layer)
from engine import MAX_ASSET_PIXELS
from preview import PreviewRenderer
from adjustments import default_adjustment,KINDS as ADJUSTMENT_KINDS,LABELS as ADJUSTMENT_LABELS
from engine import translate_group,validate_structure
from dialogs import TextDialog, AdjustmentDialog, text_image
from jobs import JobRunner, filtered_document, keyed_document, segmented_document
from segmentation import Segmenter
from psdio import import_documents, load_psd

BG, PANEL, TEXT, ACCENT = "#191c23", "#232833", "#dbe2ed", "#67afff"


def guarded(function):
    @functools.wraps(function)
    def wrapped(self, *args, **kwargs):
        try:
            if hasattr(self,"jobs") and self.jobs.current and self.jobs.current.lock_document and function.__name__ not in ("draw","paint_view","save","export","export_psd"):
                self.status.set("正在"+self.jobs.current.label+"，仍可缩放和平移画布。")
                return
            return function(self, *args, **kwargs)
        except Exception as error:
            if self.live_before is not None and self.modal_edit is None:
                self.finish_live_edit(False)
            self.status.set(str(error))
            messagebox.showerror("操作未完成", str(error), parent=self.root)
    return wrapped


class Editor:
    def __init__(self, root):
        self.root = root
        # Tk objects must be finalized on the Tk thread. Cyclic GC otherwise may
        # run during a worker allocation and destroy a closed dialog on that thread.
        self.gc_was_enabled = gc.isenabled()
        gc.disable()
        self.gc_job = root.after(3000, self.collect_garbage)
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
        self.scene_revision = 0
        self.preview_renderer = PreviewRenderer()
        self.preview_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preview")
        self.preview_future = None
        self.preview_pending = None
        self.preview_request = None
        self.preview_signature = None
        self.preview_rgb = None
        self.preview_document_id = None
        self.preview_poll_job = None
        self.preview_settle_job = None
        self.tile_future = None
        self.tile_request = None
        self.tile_signature = None
        self.tile_images = {}
        self.tile_photos = {}
        self.tile_poll_job = None
        self.jobs = JobRunner()
        self.job_poll_job = None
        self.segmenter = Segmenter()
        self.fast_segmenter = Segmenter(model="u2netp")
        self.photo_signature = None
        self.interacting = False
        self.destroyed = False
        self.modal_edit = None
        self.live_before = None
        self.handle_points = []
        self.drag_mode = "move"
        self.status = tk.StringVar(value="导入图片、RAW 或分层 PSD 开始编辑；所有文件操作在后台运行。")
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
                         ("导入图片 / PSD…", self.import_images, "Ctrl+I"), ("导入 PSD 合成预览…",self.import_psd_preview,""), (None, None, ""),
                         ("保存工程", self.save, "Ctrl+S"), ("另存为…", lambda: self.save(True), "Ctrl+Shift+S"),
                         ("导出 PNG / JPEG…", self.export, "Ctrl+Shift+E"), ("导出分层 PSD…", self.export_psd, ""),
                         ("导出兼容 PSD（合并受影响图层）…",lambda:self.export_psd(True),""),
                         ("RAW 显影导出 16 位 TIFF…",self.export_raw_tiff,""), (None, None, ""), ("退出", self.close, "")])
        section("编辑", [("撤销", self.undo, "Ctrl+Z"), ("重做", self.redo, "Ctrl+Shift+Z"),
                         ("复制图层", self.duplicate_layer, "Ctrl+J"), ("删除图层", self.delete_layer, "Delete")])
        section("图层", [("新建透明图层", self.blank_layer, ""), ("添加 / 编辑文字…", self.add_text, "T"),
                         ("编辑当前调整层…", self.edit_adjustment, ""),
                         ("编辑图层效果…", self.edit_effects, ""), ("新建图层组",self.new_group,""), ("移入图层组…",self.move_to_group,""),
                         ("重命名…", self.rename_layer, ""), ("显示 / 隐藏", self.toggle_visible, ""),
                         ("添加白色蒙版", self.add_mask, ""), ("启用 / 禁用蒙版", self.toggle_mask, ""),
                         ("反相蒙版", self.invert_mask, ""), ("水平翻转", lambda: self.flip("x"), ""),
                         ("垂直翻转", lambda: self.flip("y"), "")])
        section("调整层",[(ADJUSTMENT_LABELS[kind]+("…" if kind!="Invert" else ""),lambda k=kind:self.new_adjustment(k),"") for kind in ADJUSTMENT_KINDS])
        section("图像", [("亮度…", lambda: self.adjust("brightness"), ""), ("对比度…", lambda: self.adjust("contrast"), ""),
                         ("饱和度…", lambda: self.adjust("saturation"), ""), ("高斯模糊…", lambda: self.adjust("blur"), ""),
                         ("自动对比度", lambda: self.adjust("autocontrast"), ""), ("灰度", lambda: self.adjust("grayscale"), ""),
                         ("反相", lambda: self.adjust("invert"), ""), (None, None, ""),
                         ("AI 主体分割 → 蒙版",self.segment_subject,""),
                         ("AI 主体分割（快速）",lambda:self.segment_subject(True),""),
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
        ttk.Label(top, text="Windows 预览版 0.4", foreground="#97a4b9").pack(side="right")
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
        self.tree.bind("<Double-1>", self.layer_double_click)
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
        ttk.Label(right, text="角点缩放 · 圆点旋转 · Shift 约束\n双击：编辑文字或调整层\n滚轮缩放 · 右键拖动平移", foreground="#96a5b9", justify="left").pack(anchor="w", pady=10)
        self.canvas = tk.Canvas(body, bg="#12151b", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas_image = self.canvas.create_image(0, 0, anchor="nw")
        self.canvas_border = self.canvas.create_rectangle(0, 0, 0, 0, outline="#68748b")
        self.canvas_outline = self.canvas.create_polygon(0, 0, 0, 0, outline=ACCENT, fill="", width=1, dash=(4, 3))
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
        self.progress = ttk.Progressbar(footer,mode="indeterminate",length=80)
        self.cancel_job_button = ttk.Button(footer,text="取消",command=self.cancel_job,width=5)

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
        if self.modal_edit: return
        if isinstance(self.root.focus_get(), (tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox, tk.Text)):
            return
        callback()
        return "break"

    def checkpoint(self):
        # Editing actions replace raster assets; metadata-only gestures need no pixel copy.
        self.history.push(self.document, share_assets=True)
        self.dirty = True

    def changed(self, text="已更新。"):
        self.dirty = True
        self.status.set(text)
        self.refresh()

    def refresh(self):
        self.scene_revision += 1
        self.root.title(f"{'* ' if self.dirty else ''}{self.path.stem if self.path else '未命名'} · Compositor Windows")
        selection = self.document.active_id
        self.tree.delete(*self.tree.get_children())
        lookup = {layer.id: layer for layer in self.document.layers}
        for layer in reversed(self.document.layers):
            depth, parent = 0, layer.parent_id
            while parent:
                depth += 1
                parent = lookup[parent].parent_id
            symbol = "▣ " if layer.is_group else "◐ " if layer.metadata.get("adjustment") else "T " if layer.metadata.get("text") else ""
            name = "  "*depth + symbol + layer.name + (" [蒙版]" if layer.mask else "")
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
        self.paint_view()
        if not self.interacting: self.request_tiles()

    def update_inspector(self):
        layer = self.document.active
        values = [layer.transform.x, layer.transform.y, layer.transform.width, layer.transform.height,
                  layer.transform.rotation, layer.opacity*100] if layer else [0, 0, 0, 0, 0, 100]
        for key, value in zip(self.inspector, values): self.inspector[key].set(f"{value:g}")
        self.blend.set(layer.blend if layer else "Normal")

    def schedule_render(self):
        # Throttle without resetting the deadline; repeated mouse moves must not starve frames.
        if self.refresh_job is None and not self.destroyed:
            self.refresh_job = self.root.after(16, self.draw)

    def interaction(self):
        self.interacting = True
        if self.preview_settle_job: self.root.after_cancel(self.preview_settle_job)
        self.preview_settle_job = self.root.after(160, self.settle_preview)

    def settle_preview(self):
        if self.preview_settle_job:
            self.root.after_cancel(self.preview_settle_job)
        self.preview_settle_job = None
        self.interacting = False
        self.schedule_render()

    @guarded
    def draw(self):
        if self.refresh_job:
            self.root.after_cancel(self.refresh_job)
        self.refresh_job = None
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        if self.fit_mode:
            self.zoom = max(.02, min((width-60)/self.document.width, (height-60)/self.document.height, 2))
            self.offset = ((width-self.document.width*self.zoom)/2, (height-self.document.height*self.zoom)/2)
        # Navigation transforms the existing flattened preview immediately. Recomposition
        # runs off the Tk thread and converges to a high-quality frame after the gesture.
        fast = self.interacting
        bound = (1000, 700) if fast else (1600, 1100)
        target = (min(bound[0], max(1, round(self.document.width*self.zoom))),
                  min(bound[1], max(1, round(self.document.height*self.zoom))))
        signature = (self.document.id, self.scene_revision, target, fast)
        self.preview_request = signature
        if signature != self.preview_signature:
            if not self.preview_future:
                self.start_preview(signature, self.document.snapshot())
            elif signature != self.preview_future[0]:
                # Only the most recent request waits; mouse events never create a queue.
                self.preview_pending = (signature, self.document.snapshot())
        self.paint_view()
        self.request_tiles()

    def start_preview(self, signature, snapshot):
        if self.destroyed: return
        _, _, target, fast = signature
        future = self.preview_worker.submit(self.preview_renderer.draw, snapshot, target, fast)
        self.preview_future = (signature, future)
        if self.preview_poll_job is None:
            self.preview_poll_job = self.root.after(8, self.poll_preview)

    def poll_preview(self):
        self.preview_poll_job = None
        if self.destroyed or not self.preview_future: return
        signature, future = self.preview_future
        if not future.done():
            self.preview_poll_job = self.root.after(8, self.poll_preview)
            return
        self.preview_future = None
        try:
            image = future.result()
            # During a gesture show completed frames, then catch up with the newest one.
            # Outside a gesture ignore superseded results (e.g. after Undo or Open).
            if signature == self.preview_request or (self.interacting and signature[0] == self.document.id):
                self.preview_rgb = image
                self.preview_document_id = signature[0]
                self.preview_signature = signature
                self.paint_view()
                self.request_tiles()
        except Exception as error:
            self.status.set(f"预览未完成：{error}")
        pending, self.preview_pending = self.preview_pending, None
        if pending and pending[0] == self.preview_request:
            self.start_preview(*pending)
        elif signature != self.preview_request:
            self.schedule_render()

    @guarded
    def paint_view(self):
        if self.destroyed: return
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        visible = self.preview_rgb if self.preview_document_id == self.document.id else None
        screen_size = (max(1, round(self.document.width*self.zoom)), max(1, round(self.document.height*self.zoom)))
        # Avoid unbounded zoom bitmaps. Draw the visible canvas region only.
        ox, oy = self.offset
        left, top = max(0, -ox), max(0, -oy)
        right, bottom = min(screen_size[0], width-ox), min(screen_size[1], height-oy)
        if visible is not None and right > left and bottom > top:
            sx, sy = visible.width/screen_size[0], visible.height/screen_size[1]
            whole = screen_size[0]*screen_size[1] <= 4_000_000
            photo_key = (id(visible), screen_size, None if whole else (left, top, right, bottom))
            if photo_key != self.photo_signature:
                if whole:
                    cropped = visible.resize(screen_size, Image.Resampling.BILINEAR)
                else:
                    cropped = visible.transform((max(1, round(right-left)), max(1, round(bottom-top))), Image.Transform.AFFINE,
                                                (sx, 0, left*sx, 0, sy, top*sy), resample=Image.Resampling.BILINEAR)
                self.photo = ImageTk.PhotoImage(cropped)
                self.photo_signature = photo_key
                self.canvas.itemconfigure(self.canvas_image, image=self.photo)
            self.canvas.coords(self.canvas_image, ox if whole else ox+left, oy if whole else oy+top)
            self.canvas.itemconfigure(self.canvas_image, state="normal")
        else:
            self.canvas.itemconfigure(self.canvas_image, state="hidden")
        self.canvas.coords(self.canvas_border, ox, oy, ox+screen_size[0], oy+screen_size[1])
        layer = self.document.active
        self.handle_points = []
        self.canvas.delete("handles")
        if layer and not layer.is_group and layer.image is not None:
            t, points = layer.transform, []
            for u, v in ((0, 0), (1, 0), (1, 1), (0, 1)):
                a = math.radians(t.rotation)
                dx, dy = (u-.5)*t.width, (v-.5)*t.height
                points.extend((ox+(t.x+t.width/2+dx*math.cos(a)-dy*math.sin(a))*self.zoom,
                               oy+(t.y+t.height/2+dx*math.sin(a)+dy*math.cos(a))*self.zoom))
            self.canvas.coords(self.canvas_outline, *points)
            self.canvas.itemconfigure(self.canvas_outline, state="normal")
            if self.tool.get() == "move":
                for index in range(4):
                    x, y = points[index*2:index*2+2]
                    self.handle_points.append((x, y, index))
                    self.canvas.create_rectangle(x-4, y-4, x+4, y+4, fill="#e8f2ff", outline=ACCENT, tags="handles")
                center_top = ((points[0]+points[2])/2, (points[1]+points[3])/2)
                rx, ry = center_top[0]+math.sin(math.radians(t.rotation))*25, center_top[1]-math.cos(math.radians(t.rotation))*25
                self.handle_points.append((rx, ry, "rotate"))
                self.canvas.create_line(*center_top, rx, ry, fill=ACCENT, tags="handles")
                self.canvas.create_oval(rx-5, ry-5, rx+5, ry+5, fill=ACCENT, outline="white", tags="handles")
        else:
            self.canvas.itemconfigure(self.canvas_outline, state="hidden")
        self.draw_crop()
        self.info.set(f"{self.document.width} × {self.document.height}   {self.zoom*100:.0f}%")
        self.paint_tiles()

    def viewport(self):
        ox,oy = self.offset
        return (max(0,math.floor(-ox/self.zoom)),max(0,math.floor(-oy/self.zoom)),
                min(self.document.width,math.ceil((self.canvas.winfo_width()-ox)/self.zoom)),
                min(self.document.height,math.ceil((self.canvas.winfo_height()-oy)/self.zoom)))

    def request_tiles(self):
        if self.destroyed: return
        if self.zoom<1:
            self.tile_request=None
            self.canvas.delete("native-tile"); self.tile_photos={}
            return
        if self.interacting or self.preview_future: return
        region = self.viewport()
        if region[2]<=region[0] or region[3]<=region[1]: return
        from tiles import TILE_SIZE
        cells = (region[0]//TILE_SIZE,region[1]//TILE_SIZE,(region[2]-1)//TILE_SIZE,(region[3]-1)//TILE_SIZE)
        signature = (self.document.id,self.scene_revision,cells)
        self.tile_request = signature
        if self.tile_future or self.tile_signature==signature: return
        self.tile_future = (signature,self.preview_worker.submit(self.preview_renderer.draw_tiles,self.document.snapshot(),region))
        if self.tile_poll_job is None: self.tile_poll_job=self.root.after(12,self.poll_tiles)

    def poll_tiles(self):
        self.tile_poll_job=None
        if self.destroyed or self.tile_future is None: return
        signature,future = self.tile_future
        if not future.done(): self.tile_poll_job=self.root.after(12,self.poll_tiles); return
        self.tile_future=None
        try:
            result=future.result()
            if signature==self.tile_request and signature[:2]==(self.document.id,self.scene_revision):
                self.tile_images=result; self.tile_signature=signature; self.paint_view()
        except Exception as error: self.status.set("原像素预览未完成："+str(error))
        self.request_tiles()

    def paint_tiles(self):
        valid = self.zoom>=1 and self.tile_signature and self.tile_signature[:2]==(self.document.id,self.scene_revision)
        self.canvas.delete("native-tile")
        if not valid:
            self.tile_photos={}; return
        used={}
        ox,oy=self.offset
        region=self.viewport()
        for box,image in self.tile_images.items():
            if box[2]<=region[0] or box[0]>=region[2] or box[3]<=region[1] or box[1]>=region[3]: continue
            # Only the visible piece becomes a Tk bitmap, even at 800% zoom.
            left,top=max(box[0],region[0]),max(box[1],region[1])
            right,bottom=min(box[2],region[2]),min(box[3],region[3])
            screen_left,screen_top=round(ox+left*self.zoom),round(oy+top*self.zoom)
            size=(max(1,round(ox+right*self.zoom)-screen_left),max(1,round(oy+bottom*self.zoom)-screen_top))
            key=(id(image),self.zoom,left,top,right,bottom,size)
            photo=self.tile_photos.get(key)
            if photo is None:
                cropped=image.crop((left-box[0],top-box[1],right-box[0],bottom-box[1])).resize(size,Image.Resampling.NEAREST)
                photo=ImageTk.PhotoImage(cropped)
            used[key]=photo
            item=self.canvas.create_image(screen_left,screen_top,image=photo,anchor="nw",tags="native-tile")
            self.canvas.tag_lower(item,self.canvas_border)
        self.tile_photos=used
        if used: self.info.set(f"{self.document.width} × {self.document.height}   {self.zoom*100:.0f}% · 原像素分块")

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
        if self.jobs.current and self.jobs.current.lock_document: return
        self.canvas.focus_set()
        point = self.doc_point(event)
        self.press_point = self.previous_point = point
        if self.tool.get() == "crop":
            self.crop_box = (*point, *point)
            self.draw_crop()
            return
        layer = self.document.active
        if not layer or (layer.is_group and self.tool.get()!="move" and not self.mask_target.get()): self.press_point = None; return
        if self.tool.get() in ("brush", "erase") and layer.image is None and not self.mask_target.get():
            self.press_point = None
            raise ValueError("请选择有像素的图层。")
        self.checkpoint()
        self.interaction()
        if self.tool.get() == "move":
            self.drag_origin = (layer.transform.x, layer.transform.y)
            self.drag_transform = copy.copy(layer.transform)
            self.drag_mode = "move"
            for x, y, handle in self.handle_points:
                if math.hypot(event.x-x, event.y-y) <= 10:
                    self.drag_mode = handle
                    break
            if self.drag_mode == "rotate":
                cx, cy = layer.transform.x+layer.transform.width/2, layer.transform.y+layer.transform.height/2
                self.drag_angle = math.degrees(math.atan2(point[1]-cy, point[0]-cx))
        else:
            self.paint(point, point)
        self.schedule_render()

    def paint(self, start, end):
        brush(self.document.active, start, end, max(1, min(250, self.radius.get())), self.color,
              erase=self.tool.get() == "erase", on_mask=self.mask_target.get())
        self.scene_revision += 1

    @guarded
    def pointer_move(self, event):
        if self.jobs.current and self.jobs.current.lock_document: return
        if self.press_point is None: return
        point = self.doc_point(event)
        if self.tool.get() == "crop":
            self.crop_box = (*self.press_point, *point)
            self.draw_crop()
            return
        layer = self.document.active
        if not layer or (layer.is_group and self.tool.get()!="move" and not self.mask_target.get()): return
        self.interaction()
        if self.tool.get() == "move":
            t = self.drag_transform
            constrained = bool(event.state & 1)
            if self.drag_mode == "rotate":
                cx, cy = t.x+t.width/2, t.y+t.height/2
                angle = math.degrees(math.atan2(point[1]-cy, point[0]-cx))-self.drag_angle+t.rotation
                layer.transform.rotation = round(angle/15)*15 if constrained else round(angle, 2)
            elif isinstance(self.drag_mode, int):
                u, v = ((0, 0), (1, 0), (1, 1), (0, 1))[self.drag_mode]
                c, s = math.cos(math.radians(t.rotation)), math.sin(math.radians(t.rotation))
                ax = t.x+t.width/2+(.5-u)*t.width*c-(.5-v)*t.height*s
                ay = t.y+t.height/2+(.5-u)*t.width*s+(.5-v)*t.height*c
                dx, dy = point[0]-ax, point[1]-ay
                sign_x, sign_y = 1 if u else -1, 1 if v else -1
                w = max(1, sign_x*(dx*c+dy*s))
                h = max(1, sign_y*(-dx*s+dy*c))
                if constrained:
                    ratio = t.width/t.height
                    w = max(w, h*ratio)
                    h = w/ratio
                cx = ax+sign_x*w*c/2-sign_y*h*s/2
                cy = ay+sign_x*w*s/2+sign_y*h*c/2
                layer.transform = Transform(cx-w/2, cy-h/2, w, h, t.rotation, t.flip_x, t.flip_y, t.sampling)
            else:
                dx, dy = point[0]-self.press_point[0], point[1]-self.press_point[1]
                if constrained:
                    if abs(dx) > abs(dy): dy = 0
                    else: dx = 0
                if layer.is_group:translate_group(self.document,layer.id,self.drag_origin[0]+dx-layer.transform.x,self.drag_origin[1]+dy-layer.transform.y)
                else:layer.transform.x = self.drag_origin[0]+dx;layer.transform.y = self.drag_origin[1]+dy
            self.scene_revision += 1
        else: self.paint(self.previous_point, point)
        self.previous_point = point
        self.schedule_render()

    def pointer_up(self, event):
        if self.press_point is not None and self.tool.get() != "crop": self.changed()
        elif self.tool.get() == "crop": self.status.set("按 Enter 或选择“图像 > 按矩形裁剪画布”确认；Esc 取消。")
        self.press_point = None
        self.settle_preview()

    def pan_down(self, event):
        self.fit_mode = False
        self.pan_start = (event.x, event.y, *self.offset)

    def pan_move(self, event):
        x, y, ox, oy = self.pan_start
        self.offset = (ox+event.x-x, oy+event.y-y)
        # Panning never changes layer pixels, scale, or compositing.
        self.paint_view()
        if self.zoom>=1: self.interaction(); self.schedule_render()

    def wheel(self, event):
        self.set_zoom(self.zoom*(1.15 if event.delta > 0 else 1/1.15), (event.x, event.y))

    def set_zoom(self, value, anchor=None):
        value = max(.02, min(8, value))
        anchor = anchor or (self.canvas.winfo_width()/2, self.canvas.winfo_height()/2)
        x, y = anchor
        self.offset = (x-(x-self.offset[0])*value/self.zoom, y-(y-self.offset[1])*value/self.zoom)
        self.zoom, self.fit_mode = value, False
        self.interaction()
        self.paint_view()
        self.schedule_render()

    def fit(self): self.fit_mode = True; self.schedule_render()
    def cancel_crop(self): self.crop_box = None; self.draw_crop()

    def confirm_discard(self):
        if not self.dirty: return True
        answer = messagebox.askyesnocancel("未保存的修改", "是否先保存工程？", parent=self.root)
        if answer is None: return False
        return self.save() if answer else True

    def after_discard(self,action):
        if self.jobs.current:
            self.status.set("请等待后台操作完成后再切换工程。"); return
        if not self.dirty: action(); return
        answer=messagebox.askyesnocancel("未保存的修改","是否先保存工程？",parent=self.root)
        if answer is None: return
        if answer: self.save(after=action,lock_document=True)
        else: action()

    def start_job(self,label,function,args,on_success,lock_document=False,cancellable=True):
        if self.jobs.current:
            self.status.set("正在"+self.jobs.current.label+"，请等待。"); return False
        self.jobs.start(label,function,args,on_success,lock_document,cancellable)
        self.status.set("正在"+label+"… 可继续查看画布。")
        self.progress.pack(side="right",padx=8); self.progress.start(12)
        if cancellable: self.cancel_job_button.pack(side="right",padx=4)
        self.job_poll_job=self.root.after(15,self.poll_job)
        return True

    def poll_job(self):
        self.job_poll_job=None
        if self.destroyed or not self.jobs.current: return
        job=self.jobs.current
        if not job.future.done(): self.job_poll_job=self.root.after(15,self.poll_job); return
        self.jobs.current=None
        self.progress.stop(); self.progress.pack_forget(); self.cancel_job_button.pack_forget()
        try:
            if job.cancelled: self.status.set("已取消"+job.label+"，工程保持原状。"); return
            result=job.future.result()
            job.on_success(result)
        except Exception as error:
            self.status.set(job.label+"未完成："+str(error))
            messagebox.showerror("操作未完成",str(error),parent=self.root)

    def cancel_job(self):
        self.jobs.cancel(); self.status.set("正在取消… 当前计算结束后丢弃结果。")

    @guarded
    def new_document(self):
        width = simpledialog.askinteger("新建画布", "宽度（像素）", initialvalue=1280, minvalue=1, maxvalue=12000, parent=self.root)
        if width is None: return
        height = simpledialog.askinteger("新建画布", "高度（像素）", initialvalue=800, minvalue=1, maxvalue=12000, parent=self.root)
        if height is None: return
        dimensions(width, height)
        def create():
            self.document, self.history = Document(width,height),History()
            self.path,self.dirty,self.crop_box=None,False,None
            self.fit_mode=True; self.refresh()
        self.after_discard(create)

    @guarded
    def open_document(self):
        path = filedialog.askdirectory(title="选择 .comp 工程文件夹（内部含 manifest.json）", parent=self.root)
        if not path: return
        def install(document):
            self.document,self.history,self.path=document,History(),Path(path)
            self.dirty,self.crop_box,self.fit_mode=False,None,True
            self.refresh(); self.status.set("已打开工程。")
        self.after_discard(lambda:self.start_job("打开工程",load_project,(path,),install,True))

    @guarded
    def import_psd_preview(self):
        path=filedialog.askopenfilename(title="导入 PSD 的合成预览（单层）",filetypes=[("Photoshop 工程","*.psd *.psb")],parent=self.root)
        if not path:return
        def prepare(snapshot):
            image=load_image(path)
            if not snapshot.layers:snapshot.width,snapshot.height=image.size
            snapshot.add(image,Path(path).stem+" · PSD 合成预览")
            return snapshot
        def install(candidate):
            self.checkpoint();self.document=candidate;self.fit_mode=True;self.changed("已导入 PSD 合成预览（单层），原 PSD 保持不变。")
        self.start_job("导入 PSD 合成预览",prepare,(self.document.snapshot(),),install,True)

    @guarded
    def import_images(self):
        paths = filedialog.askopenfilenames(title="导入图片 / 分层 PSD / RAW", filetypes=[("图片 / PSD / RAW", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.gif *.psd *.psb *.dng *.cr2 *.cr3 *.nef *.arw *.raf *.orf *.rw2 *.pef *.kdc *.raw"), ("全部文件", "*.*")], parent=self.root)
        if not paths: return
        from rawio import RAW_EXTENSIONS
        options={}
        if any(Path(p).suffix.lower() in RAW_EXTENSIONS for p in paths):
            from extended_dialogs import RawDialog
            options=RawDialog(self.root).result
            if options is None: return
        def install(result):
            candidate,notes=result
            self.checkpoint(); self.document=candidate; self.fit_mode=True
            self.changed(f"已导入 {len(paths)} 个文件，PSD 保留独立图层。")
            if notes:
                self.status.set(f"导入完成，有 {len(notes)} 项格式转换说明。")
                messagebox.showinfo("PSD 转换说明","\n".join(notes),parent=self.root)
        self.start_job("导入文件",import_documents,(self.document.snapshot(),paths,options),install,True)

    @guarded
    def save(self, as_new=False, after=None, lock_document=False):
        path = self.path
        if as_new or path is None:
            result = filedialog.asksaveasfilename(title="保存 .comp 工程（将创建文件夹）", defaultextension=".comp", filetypes=[("Compositor 工程", "*.comp")], parent=self.root)
            if not result: return False
            path = Path(result)
        signature=(self.document.id,self.scene_revision)
        snapshot=self.document.snapshot()
        def saved(result):
            if self.document.id==signature[0]:
                self.path=Path(path)
                if self.scene_revision==signature[1]: self.dirty=False
                self.refresh()
            self.status.set(f"工程已保存：{path}"+("；后续编辑尚未保存。" if self.dirty else ""))
            if after: after()
        self.start_job("保存工程",save_project,(snapshot,path),saved,lock_document,False)
        return False  # A pending save must never authorize discarding the live document.

    @guarded
    def export(self):
        path = filedialog.asksaveasfilename(title="导出合成图片", defaultextension=".png", filetypes=[("PNG（保留透明）", "*.png"), ("JPEG（白色底）", "*.jpg")], parent=self.root)
        if not path: return
        self.start_job("导出图片",export_image,(self.document.snapshot(),path),lambda _:self.status.set(f"图片已导出：{path}"),False,False)

    @guarded
    def export_raw_tiff(self):
        from rawio import export_raw_tiff,RAW_EXTENSIONS
        from extended_dialogs import RawDialog
        source=filedialog.askopenfilename(title="选择 RAW 原文件，直接显影为 16 位 TIFF",filetypes=[("相机 RAW"," ".join("*"+ext for ext in RAW_EXTENSIONS)),("全部文件","*.*")],parent=self.root)
        if not source:return
        options=RawDialog(self.root,16).result
        if options is None:return
        target=filedialog.asksaveasfilename(title="16 位 RAW 显影输出",defaultextension=".tif",filetypes=[("16 位 RGB TIFF","*.tif *.tiff")],parent=self.root)
        if not target:return
        def run():return export_raw_tiff(source,target,**options)
        self.start_job("RAW 16 位显影导出",run,(),lambda size:self.status.set(f"已导出 {size[0]}×{size[1]} 的 16 位 TIFF：{target}"),False,False)

    @guarded
    def export_psd(self,compatible=False):
        from psdexport import export_psd
        path=filedialog.asksaveasfilename(title="导出分层 PSD",defaultextension=".psd",filetypes=[("Photoshop 分层工程","*.psd")],parent=self.root)
        if not path: return
        def done(notes):
            self.status.set(f"分层 PSD 已导出：{path}")
            if notes: messagebox.showinfo("PSD 导出说明","\n".join(notes),parent=self.root)
        self.start_job("导出分层 PSD",export_psd,(self.document.snapshot(),path,compatible),done,False,False)

    def undo(self):
        if self.jobs.current and self.jobs.current.lock_document: return
        if self.history.undo_stack: self.document = self.history.undo(self.document); self.changed("已撤销。")

    def redo(self):
        if self.jobs.current and self.jobs.current.lock_document: return
        if self.history.redo_stack: self.document = self.history.redo(self.document); self.changed("已重做。")

    def require_layer(self, pixels=False):
        layer = self.document.active
        if layer is None or (pixels and (layer.is_group or layer.image is None)):
            raise ValueError("请先选择一个像素图层。" if pixels else "请先选择图层。")
        return layer

    @guarded
    def blank_layer(self):
        candidate = self.document.snapshot()
        selected=candidate.active
        layer=candidate.add(Image.new("RGBA", (self.document.width, self.document.height)), "透明图层")
        layer.parent_id=selected.id if selected and selected.is_group else selected.parent_id if selected else None
        self.checkpoint()
        self.document = candidate
        self.changed()

    @guarded
    def duplicate_layer(self):
        layer = self.require_layer(pixels=True)
        clone = copy.deepcopy(layer, {id(im): im for im in (layer.image, layer.mask) if im is not None})
        if len(self.document.layers)>=512:raise ValueError("工程最多支持 512 个图层。")
        if sum(im.width*im.height for l in self.document.layers for im in (l.image,l.mask) if im is not None)+sum(im.width*im.height for im in (clone.image,clone.mask) if im is not None)> MAX_ASSET_PIXELS:
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
        siblings=[item for item in self.document.layers if item.parent_id==layer.parent_id]
        index=siblings.index(layer)+delta
        if not 0<=index<len(siblings):return
        other=siblings[index]
        i,j=self.document.layers.index(layer),self.document.layers.index(other)
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
        if layer.is_group and (transform.width!=layer.transform.width or transform.height!=layer.transform.height or transform.rotation!=layer.transform.rotation):
            raise ValueError("图层组支持整体平移；缩放/旋转请分别编辑组内图层。")
        self.checkpoint()
        if layer.is_group:translate_group(self.document,layer.id,transform.x-layer.transform.x,transform.y-layer.transform.y)
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
        layer = self.require_layer()
        if layer.mask is None:
            self.checkpoint(); layer.mask = Image.new("L", layer.image.size if layer.image else (self.document.width, self.document.height), 255); self.changed()
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
        if self.modal_edit: return
        self.begin_live_edit()
        layer = self.document.active
        if not layer or not layer.metadata.get("text"):
            parent_id=layer.id if layer and layer.is_group else layer.parent_id if layer else None
            from PIL import ImageColor
            rgb = ImageColor.getrgb(self.color)
            style = dict(content="Text", fontName="MicrosoftYaHei", fontSize=64, red=rgb[0]/255, green=rgb[1]/255, blue=rgb[2]/255, alignment="Left", tracking=0, leading=0)
            image = text_image(style)
            layer = self.document.add(image, "文字")
            layer.parent_id=parent_id
            layer.transform.x, layer.transform.y = (self.document.width-image.width)/2, (self.document.height-image.height)/2
            layer.metadata["text"] = style
        TextDialog(self, layer)
        self.changed("文字实时预览；应用保留，取消还原。")

    def begin_live_edit(self):
        self.live_before = self.document.snapshot()
        self.live_dirty_before = self.dirty

    def finish_live_edit(self, accepted):
        before, self.live_before = self.live_before, None
        if before is None: return
        if accepted:
            self.history.push(before, share_assets=True)
            self.changed("已应用，可撤销。")
        else:
            self.document, self.dirty = before, self.live_dirty_before
            self.refresh()
            self.status.set("已取消并还原。")

    def preview_text(self, layer_id, style, image):
        layer = next(l for l in self.document.layers if l.id == layer_id)
        baseline = next((l for l in self.live_before.layers if l.id == layer_id), None)
        reference = baseline if baseline and baseline.image else layer
        wscale, hscale = reference.transform.width/reference.image.width, reference.transform.height/reference.image.height
        if sum(im.width*im.height for l in self.document.layers if l.id != layer_id for im in (l.image, l.mask) if im is not None)+image.width*image.height > MAX_ASSET_PIXELS:
            raise ValueError("文字超过源像素预算。")
        layer.image = image
        layer.transform.width, layer.transform.height = image.width*wscale, image.height*hscale
        layer.metadata["text"] = style
        layer.name = " ".join(style["content"].split())[:30] or "文字"
        self.scene_revision += 1
        self.schedule_render()

    def preview_adjustment(self, layer_id, data):
        layer = next(l for l in self.document.layers if l.id == layer_id)
        layer.metadata["adjustment"] = data
        self.scene_revision += 1
        self.schedule_render()

    @guarded
    def new_adjustment(self, kind):
        if self.modal_edit: return
        self.begin_live_edit()
        layer = Layer(kind, None, Transform(0, 0, self.document.width, self.document.height), metadata={"adjustment": default_adjustment(kind)})
        selected=self.live_before.active
        if selected:
            layer.parent_id=selected.id if selected.is_group else selected.parent_id
        self.document.layers.append(layer)
        self.document.active_id = layer.id
        AdjustmentDialog(self, layer)
        self.changed("调整层作用于下方图层，参数可随时重新编辑。")

    @guarded
    def edit_adjustment(self):
        if self.modal_edit: return
        layer = self.require_layer()
        if not layer.metadata.get("adjustment"): raise ValueError("当前图层不是调整层。")
        self.begin_live_edit()
        AdjustmentDialog(self, layer)

    def layer_double_click(self, event=None):
        layer = self.document.active
        if layer and layer.metadata.get("text"): self.add_text()
        elif layer and layer.metadata.get("adjustment"): self.edit_adjustment()
        else: self.toggle_visible()

    @guarded
    def adjust(self, kind):
        layer = self.require_layer(pixels=True)
        value = 1
        if kind in ("brightness", "contrast", "saturation", "blur"):
            value = simpledialog.askfloat("图像调整", "半径（0–100）" if kind == "blur" else "系数（0–5，1 为原始值）", initialvalue=3 if kind == "blur" else 1,
                                          minvalue=0, maxvalue=100 if kind == "blur" else 5, parent=self.root)
            if value is None: return
        def install(candidate):
            self.checkpoint(); self.document=candidate; self.changed("已调整像素，可撤销。")
        self.start_job("像素滤镜",filtered_document,(self.document.snapshot(),layer.id,kind,value),install,True)

    @guarded
    def remove_color(self):
        layer = self.require_layer(pixels=True)
        color = colorchooser.askcolor(title="选择要隐藏的背景颜色（适合纯色背景）", parent=self.root)[0]
        if not color: return
        tolerance = simpledialog.askfloat("按颜色抠图", "颜色容差（1–120）", initialvalue=25, minvalue=1, maxvalue=120, parent=self.root)
        if tolerance is None: return
        def install(candidate):
            self.checkpoint(); self.document=candidate; self.mask_target.set(True); self.changed("已按颜色生成蒙版，可用画笔/橡皮修整。")
        self.start_job("颜色抠图",keyed_document,(self.document.snapshot(),layer.id,color,tolerance),install,True)

    @guarded
    def segment_subject(self, fast=False):
        layer=self.require_layer(pixels=True)
        def install(result):
            candidate,provider=result
            self.checkpoint(); self.document=candidate; self.mask_target.set(True)
            self.changed("AI 主体蒙版已生成，可用画笔修整。推理设备："+provider)
        self.start_job("AI 主体分割",segmented_document,(self.document.snapshot(),layer.id,self.fast_segmenter if fast else self.segmenter),install,True)

    @guarded
    def edit_effects(self):
        if self.modal_edit: return
        layer=self.require_layer(pixels=True)
        from extended_dialogs import EffectsDialog
        self.begin_live_edit(); EffectsDialog(self,layer)

    @guarded
    def new_group(self):
        if len(self.document.layers)>=512:raise ValueError("工程最多支持 512 个图层。")
        self.checkpoint()
        parent=self.document.active
        layer=Layer("新建组",None,Transform(0,0,self.document.width,self.document.height),is_group=True,
                    parent_id=parent.id if parent and parent.is_group else parent.parent_id if parent else None)
        self.document.layers.append(layer); self.document.active_id=layer.id; self.changed()

    @guarded
    def move_to_group(self):
        layer=self.require_layer()
        from extended_dialogs import GroupDialog
        parent=GroupDialog(self.root,self.document,layer).result
        if parent is False: return
        candidate=self.document.snapshot();candidate.active.parent_id=parent;validate_structure(candidate)
        self.checkpoint();self.document=candidate;self.changed("已移动图层，可撤销。")

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
        messagebox.showinfo("快速使用", "1. 新建画布并导入图片\n2. V 移动，角点缩放、圆点旋转，Shift 约束比例或角度\n3. 滚轮缩放，右键拖动平移；Ctrl+0 适合窗口\n4. T 添加/编辑文字，双击文字或调整层重新编辑\n5. “调整层”菜单添加曝光、色阶、曲线、反相或模糊\n6. B 画笔、E 橡皮；勾选编辑蒙版可修整蒙版\n7. Ctrl+S 保存 .comp；Ctrl+Shift+E 导出 PNG/JPEG\n\n完整说明见发行文件夹中的 README-中文.md。", parent=self.root)

    def about(self):
        messagebox.showinfo("关于", "Compositor Windows 0.4\n独立 Windows 移植预览版，非上游官方发行版。\nMIT 开源。\n\n"+self.preview_renderer.backend_label+"\n本地 AI 分割、RAW / 16 位 TIFF、分层 PSD 与兼容导出\n12 种调整、六种图层效果、文字排版、原像素分块和后台操作。\n兼容范围与验证记录见 README-中文.md。", parent=self.root)

    def callback_error(self, kind, error, trace):
        traceback.print_exception(kind, error, trace)
        messagebox.showerror("界面错误", str(error), parent=self.root)

    def close(self):
        if self.modal_edit: self.modal_edit.cancel()
        def finish():
            self.shutdown()
            self.root.destroy()
        self.after_discard(finish)

    def shutdown(self):
        if self.modal_edit: self.modal_edit.cancel()
        self.destroyed = True
        for job in (self.refresh_job, self.preview_poll_job, self.preview_settle_job, self.gc_job,self.tile_poll_job,self.job_poll_job):
            if job:
                try: self.root.after_cancel(job)
                except tk.TclError: pass
        self.jobs.shutdown()
        self.preview_worker.submit(self.preview_renderer.close).result()
        self.preview_worker.shutdown(wait=True, cancel_futures=True)
        gc.collect()
        if self.gc_was_enabled: gc.enable()

    def collect_garbage(self):
        if self.destroyed: return
        if not self.interacting: gc.collect()
        self.gc_job = self.root.after(3000, self.collect_garbage)


def smoke_test(output, project=None, exercise_features=False):
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
    if exercise_features:
        original_count = len(editor.document.layers)
        editor.add_text()
        dialog = editor.modal_edit
        dialog.content.delete("1.0", "end")
        dialog.content.insert("1.0", "Windows 0.2 validation")
        dialog.size.set("32")
        dialog.accept()
        if editor.document.active.metadata.get("text", {}).get("content") != "Windows 0.2 validation":
            raise RuntimeError("Editable text validation failed")
        editor.undo()
        editor.new_adjustment("Levels")
        dialog = editor.modal_edit
        dialog.values["black"].set(12)
        dialog.accept()
        if editor.document.active.metadata.get("adjustment", {}).get("kind") != "Levels":
            raise RuntimeError("Adjustment editor validation failed")
        editor.add_mask()
        editor.tool.set("erase")
        editor.paint((30, 30), (35, 35))
        if editor.document.active.mask.getpixel((30, 30)) != 0:
            raise RuntimeError("Adjustment mask painting validation failed")
        editor.undo()
        editor.undo()
        editor.tool.set("move")
        if len(editor.document.layers) != original_count:
            raise RuntimeError("Feature undo validation failed")
    editor.draw()
    deadline = time.perf_counter()+15
    while editor.preview_signature != editor.preview_request and time.perf_counter() < deadline:
        root.update()
        time.sleep(.005)
    if editor.preview_signature != editor.preview_request:
        raise RuntimeError("Background preview did not finish")
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
                                                       "layers": len(editor.document.layers), "frozen": bool(getattr(sys, "frozen", False)),
                                                       "feature_editors": exercise_features}), encoding="utf-8")
    editor.shutdown()
    root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project", nargs="?")
    parser.add_argument("--smoke-test", metavar="OUTPUT_FOLDER")
    parser.add_argument("--exercise-features", action="store_true")
    parser.add_argument("--verify-v03",metavar="OUTPUT_FOLDER")
    parser.add_argument("--verify-v04",metavar="OUTPUT_FOLDER")
    parser.add_argument("--assets",metavar="FIXTURE_FOLDER")
    args = parser.parse_args()
    if args.verify_v03 or args.verify_v04:
        output=args.verify_v04 or args.verify_v03
        try:
            if args.verify_v04:
                from validation_v04 import verify
            else:
                from validation_v03 import verify
            verify(output,args.assets)
        except Exception:
            import traceback
            Path(output).mkdir(parents=True,exist_ok=True)
            (Path(output)/"verification-error.txt").write_text(traceback.format_exc(),encoding="utf-8")
            raise SystemExit(1)
        return
    if args.smoke_test:
        smoke_test(args.smoke_test, args.project, args.exercise_features)
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
