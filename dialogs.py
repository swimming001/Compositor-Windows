"""Live, cancellable editors. Transactions and rendering remain in the host editor."""
import copy
import tkinter as tk
from tkinter import ttk, colorchooser
from PIL import Image, ImageColor, ImageDraw, ImageFont

from engine import dimensions
from adjustments import default_adjustment, validate_adjustment
from typography import text_image, installed_fonts, utf16_length, replace_content, set_range


FONT_FILES = {"MicrosoftYaHei": "msyh.ttc", "ArialMT": "arial.ttf", "SegoeUI": "segoeui.ttf",
              "TimesNewRomanPSMT": "times.ttf", "Consolas": "consola.ttf"}


def legacy_text_image(style):
    font_size = float(style["fontSize"])
    if not 1 <= font_size <= 512: raise ValueError("字号范围为 1–512。")
    content = style["content"]
    if not isinstance(content, str) or len(content) > 10000: raise ValueError("文字内容过长。")
    font = ImageFont.truetype("C:/Windows/Fonts/"+FONT_FILES.get(style["fontName"], "msyh.ttc"), round(font_size))
    box = ImageDraw.Draw(Image.new("RGB", (1, 1))).multiline_textbbox((0, 0), content or " ", font=font, spacing=round(font_size*.2))
    width, height = max(1, round(box[2]-box[0])+24), max(1, round(box[3]-box[1])+24)
    dimensions(width, height)
    image = Image.new("RGBA", (width, height))
    rgb = tuple(round(max(0, min(1, style[k]))*255) for k in ("red", "green", "blue"))
    ImageDraw.Draw(image).multiline_text((12-box[0], 12-box[1]), content, font=font, spacing=round(font_size*.2), fill=rgb)
    return image


from live_dialog import LiveDialog


class TextDialog(LiveDialog):
    def __init__(self, editor, layer):
        super().__init__(editor, "编辑文字")
        self.window.geometry("540x610")
        self.layer_id = layer.id
        self.source_style = copy.deepcopy(layer.metadata.get("text") or {})
        self.color = "#%02x%02x%02x" % tuple(round(self.source_style.get(k, 1)*255) for k in ("red", "green", "blue"))
        ttk.Label(self.body, text="文字内容", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        self.content = tk.Text(self.body, height=5, wrap="word", bg="#151922", fg="#dbe2ed", insertbackground="white", relief="flat", font=("Microsoft YaHei UI", 12))
        self.content.pack(fill="both", expand=True, pady=(8, 12))
        self.content.insert("1.0", self.source_style.get("content", "Text"))
        self.content.bind("<KeyRelease>", self.schedule)
        row = ttk.Frame(self.body)
        row.pack(fill="x")
        self.font = tk.StringVar(value=self.source_style.get("fontName", "MicrosoftYaHei"))
        self.size = tk.StringVar(value=str(self.source_style.get("fontSize", 64)))
        ttk.Combobox(row, values=sorted(installed_fonts()), textvariable=self.font, width=22).pack(side="left")
        ttk.Spinbox(row, from_=1, to=2000, textvariable=self.size, width=6).pack(side="left", padx=8)
        ttk.Button(row, text="颜色", command=self.choose_color).pack(side="left")
        self.font.trace_add("write", self.schedule)
        self.size.trace_add("write", self.schedule)
        self.alignment = tk.StringVar(value=self.source_style.get("alignment","Left"))
        self.tracking = tk.StringVar(value=str(self.source_style.get("tracking",0)))
        self.leading = tk.StringVar(value=str(self.source_style.get("leading",0)))
        self.box_width = tk.StringVar(value=str((self.source_style.get("boxSize") or [0,0])[0]))
        self.box_height = tk.StringVar(value=str((self.source_style.get("boxSize") or [0,0])[1]))
        row = ttk.Frame(self.body); row.pack(fill="x",pady=8)
        ttk.Button(row,text="选区字体",command=self.range_font).pack(side="left",padx=3)
        ttk.Button(row,text="选区颜色",command=lambda:self.choose_color(True)).pack(side="left",padx=3)
        ttk.Button(row,text="清除局部样式",command=self.clear_runs).pack(side="left",padx=3)
        form = ttk.Frame(self.body); form.pack(fill="x")
        for index,(label,variable) in enumerate((("对齐",self.alignment),("字距 px",self.tracking),("行距 px（0 自动）",self.leading),("文本框宽（0 自动）",self.box_width),("文本框高（0 自动）",self.box_height))):
            ttk.Label(form,text=label).grid(row=index,column=0,sticky="w",pady=3)
            widget = ttk.Combobox(form,values=("Left","Center","Right"),textvariable=variable,state="readonly",width=16) if index==0 else ttk.Entry(form,textvariable=variable,width=18)
            widget.grid(row=index,column=1,padx=12,pady=3)
            variable.trace_add("write",self.schedule)
        ttk.Label(self.body, text="选择文字后可设置局部字体和颜色；缺失字体使用微软雅黑替代。", wraplength=470).pack(fill="x", pady=(12, 0))
        self.buttons()
        self.content.focus_set()

    def choose_color(self, selected=False):
        value = colorchooser.askcolor(self.color, parent=self.window)[1]
        if value:
            if selected:
                self.sync_content()
                a,b = self.selection_range()
                set_range(self.source_style,"colorRuns",a,b,tuple(c/255 for c in ImageColor.getrgb(value)))
            else: self.color = value; self.source_style.pop("colorRuns",None)
            self.schedule()

    def sync_content(self):
        self.source_style = replace_content(self.source_style,self.content.get("1.0","end-1c"))

    def selection_range(self):
        try:
            return (utf16_length(self.content.get("1.0","sel.first")),utf16_length(self.content.get("1.0","sel.last")))
        except tk.TclError: return 0,utf16_length(self.content.get("1.0","end-1c"))

    def range_font(self):
        self.sync_content()
        a,b = self.selection_range()
        set_range(self.source_style,"fontRuns",a,b,self.font.get())
        self.font.set(self.source_style.get("fontName","MicrosoftYaHei"))
        self.schedule()

    def clear_runs(self):
        self.source_style.pop("fontRuns",None); self.source_style.pop("colorRuns",None); self.schedule()

    def apply_preview(self):
        rgb = ImageColor.getrgb(self.color)
        self.sync_content()
        style = copy.deepcopy(self.source_style)
        style.update(content=self.content.get("1.0", "end-1c"), fontName=self.font.get(), fontSize=float(self.size.get()),
                     red=rgb[0]/255, green=rgb[1]/255, blue=rgb[2]/255, alignment=self.alignment.get(), tracking=float(self.tracking.get()), leading=float(self.leading.get()))
        width,height = float(self.box_width.get()),float(self.box_height.get())
        style.pop("boxSize",None)
        if width or height: style["boxSize"] = [width,height]
        if self.font.get()!=self.source_style.get("fontName"): style.pop("fontRuns",None)
        self.editor.preview_text(self.layer_id, style, text_image(style))


from adjustment_dialog import AdjustmentDialog
