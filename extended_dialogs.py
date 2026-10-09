"""User controls for layer effects, RAW development and folder membership."""
import copy
import tkinter as tk
from tkinter import ttk, simpledialog, colorchooser
from PIL import ImageColor
from dialogs import LiveDialog
from effects import KINDS, default_effect, validate_effects


class EffectsDialog(LiveDialog):
    def __init__(self,editor,layer):
        super().__init__(editor,"图层效果 · "+layer.name)
        self.window.geometry("500x570")
        self.layer_id=layer.id
        self.data=copy.deepcopy(layer.metadata.get("effects") or {})
        self.kind=tk.StringVar(value="stroke")
        self.values={}
        ttk.Label(self.body,text="效果可叠加；取消还原，应用后仍可重新编辑。").pack(anchor="w",pady=8)
        self.combo=ttk.Combobox(self.body,state="readonly",values=list(KINDS.values()))
        self.combo.current(0); self.combo.pack(fill="x")
        self.combo.bind("<<ComboboxSelected>>",self.select_kind)
        self.form=ttk.Frame(self.body); self.form.pack(fill="both",expand=True,pady=12)
        ttk.Button(self.body,text="移除当前效果",command=self.remove).pack(anchor="w")
        self.buttons(); self.build_form()

    def select_kind(self,event=None):
        self.store()
        self.kind.set(list(KINDS)[self.combo.current()])
        self.build_form()

    def build_form(self):
        for variable in self.values.values():
            for mode,callback in variable.trace_info(): variable.trace_remove(mode,callback)
        for child in self.form.winfo_children(): child.destroy()
        self.values={}
        kind=self.kind.get()
        data=default_effect(kind)|self.data.get(kind,{})
        if kind not in self.data: data["enabled"]=False
        labels=dict(enabled="启用效果",size="大小 px",inside="内侧描边",distance="距离 px",blur="模糊 px",angle="光源角度",opacity="不透明度 0–1")
        for key in ("enabled","inside","size","distance","blur","angle","opacity"):
            if key not in data: continue
            variable=tk.BooleanVar(value=data[key]) if key in ("enabled","inside") else tk.StringVar(value=str(data[key]))
            self.values[key]=variable
            row=ttk.Frame(self.form); row.pack(fill="x",pady=5)
            if key in ("enabled","inside"): ttk.Checkbutton(row,text=labels[key],variable=variable).pack(anchor="w")
            else:
                ttk.Label(row,text=labels[key],width=18).pack(side="left")
                ttk.Entry(row,textvariable=variable,width=16).pack(side="left")
            variable.trace_add("write",self.schedule)
        self.color="#%02x%02x%02x"%tuple(round(data[c]*255) for c in ("red","green","blue"))
        ttk.Button(self.form,text="效果颜色…",command=self.choose_color).pack(anchor="w",pady=10)

    def choose_color(self):
        value=colorchooser.askcolor(self.color,parent=self.window)[1]
        if value: self.color=value; self.schedule()

    def store(self):
        data=default_effect(self.kind.get())
        data.update({k:(v.get() if isinstance(v,tk.BooleanVar) else float(v.get())) for k,v in self.values.items()})
        data.update(zip(("red","green","blue"),(c/255 for c in ImageColor.getrgb(self.color))))
        if self.kind.get() in self.data or data["enabled"]: self.data[self.kind.get()]=data
        validate_effects(self.data)

    def remove(self):
        self.data.pop(self.kind.get(),None); self.build_form(); self.schedule()

    def apply_preview(self):
        self.store()
        layer=next(l for l in self.editor.document.layers if l.id==self.layer_id)
        if self.data: layer.metadata["effects"]=copy.deepcopy(self.data)
        else: layer.metadata.pop("effects",None)
        self.editor.scene_revision+=1; self.editor.schedule_render()


class RawDialog(simpledialog.Dialog):
    def body(self,master):
        self.exposure=tk.DoubleVar(value=0)
        self.balance=tk.StringVar(value="camera")
        self.half=tk.BooleanVar(value=False)
        ttk.Label(master,text="RAW 在后台显影为 "+("16 位 sRGB TIFF" if self.output_bits==16 else "8 位 sRGB 图层")+"；原文件保留。",wraplength=360).grid(row=0,column=0,columnspan=2,pady=10)
        ttk.Label(master,text="曝光 EV").grid(row=1,column=0,sticky="w",pady=6)
        entry=ttk.Spinbox(master,from_=-8,to=8,increment=.25,textvariable=self.exposure,width=12); entry.grid(row=1,column=1)
        ttk.Label(master,text="白平衡").grid(row=2,column=0,sticky="w",pady=6)
        ttk.Combobox(master,textvariable=self.balance,values=("camera","auto","daylight"),state="readonly",width=14).grid(row=2,column=1)
        ttk.Checkbutton(master,text="半尺寸显影（降低内存与解码时间）",variable=self.half).grid(row=3,column=0,columnspan=2,pady=10)
        return entry

    def validate(self):
        try: return -8<=self.exposure.get()<=8
        except tk.TclError: return False

    def apply(self):
        self.result=dict(exposure=self.exposure.get(),white_balance=self.balance.get(),half_size=self.half.get())

    def __init__(self,root,output_bits=8):
        self.result=None
        self.output_bits=output_bits
        super().__init__(root,"RAW 显影设置")


class GroupDialog(simpledialog.Dialog):
    def __init__(self,root,document,layer):
        self.result=False
        blocked={layer.id}
        for _ in range(65):
            more={l.id for l in document.layers if l.parent_id in blocked}
            if more<=blocked: break
            blocked |= more
        self.choices=[("根层级",None)]+[(l.name+" · "+l.id[:8],l.id) for l in document.layers if l.is_group and l.id not in blocked]
        self.current=layer.parent_id
        super().__init__(root,"移入图层组")

    def body(self,master):
        self.choice=ttk.Combobox(master,state="readonly",values=[n for n,_ in self.choices],width=34)
        self.choice.current(next((i for i,(_,key) in enumerate(self.choices) if key==self.current),0))
        self.choice.pack(padx=10,pady=20)
        return self.choice

    def apply(self): self.result=self.choices[self.choice.current()][1]
