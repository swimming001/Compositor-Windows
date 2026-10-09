"""Non-destructive adjustment controls with full channels and editable curve points."""
import copy
import tkinter as tk
from tkinter import ttk,colorchooser
from PIL import ImageColor
from live_dialog import LiveDialog
from adjustments import LABELS,validate_adjustment,default_adjustment,curve_value
from color_adjustments import RANGES,BANDS

CHANNELS=("RGB","Red","Green","Blue")


class AdjustmentDialog(LiveDialog):
    def __init__(self,editor,layer):
        super().__init__(editor,"调整层 · "+LABELS[layer.metadata["adjustment"]["kind"]])
        self.window.geometry("540x730")
        self.layer_id=layer.id;self.data=copy.deepcopy(layer.metadata["adjustment"])
        self.values={};self.colors={};self.channel=tk.StringVar(value="RGB");self.selected="RGB"
        self.kind=self.data["kind"]
        ttk.Label(self.body,text=LABELS[self.kind]+" · 可编辑调整层",font=("Microsoft YaHei UI",13,"bold")).pack(anchor="w",pady=(0,8))
        if self.kind in ("Levels","Curves","Hue/Saturation","Color Balance"):
            choices=CHANNELS if self.kind in ("Levels","Curves") else RANGES if self.kind=="Hue/Saturation" else ("shadow","mid","highlight")
            key="levels" if self.kind=="Levels" else "curves" if self.kind=="Curves" else "hsvSettings"
            initial=self.data.get(key,{})
            self.selected=initial.get("channel","RGB") if self.kind in ("Levels","Curves") else initial.get("range","Master") if self.kind=="Hue/Saturation" else "mid"
            self.channel.set(self.selected)
            ttk.Label(self.body,text="通道 / 色域 / 明暗范围").pack(anchor="w")
            combo=ttk.Combobox(self.body,textvariable=self.channel,values=choices,state="readonly")
            combo.pack(fill="x",pady=5);combo.bind("<<ComboboxSelected>>",self.change_channel)
        canvas=tk.Canvas(self.body,height=460,bg="#232833",highlightthickness=0)
        scroll=ttk.Scrollbar(self.body,orient="vertical",command=canvas.yview)
        area=ttk.Frame(self.body);area.pack(fill="both",expand=True)
        canvas.pack(in_=area,side="left",fill="both",expand=True);scroll.pack(in_=area,side="right",fill="y")
        canvas.configure(yscrollcommand=scroll.set)
        self.form=ttk.Frame(canvas);frame=canvas.create_window(0,0,window=self.form,anchor="nw")
        self.form.bind("<Configure>",lambda event:canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",lambda event:canvas.itemconfigure(frame,width=event.width))
        self.buttons();self.build_form()

    def clear_form(self):
        for var in self.values.values():
            for mode,callback in var.trace_info():var.trace_remove(mode,callback)
        for child in self.form.winfo_children():child.destroy()
        self.values={};self.colors={}

    def change_channel(self,event=None):
        try:self.store()
        except (ValueError,tk.TclError,TypeError) as error:
            self.channel.set(self.selected);self.note.set(str(error));return
        self.selected=self.channel.get();self.build_form();self.schedule()

    def slider(self,key,label,low,high,value):
        ttk.Label(self.form,text=label).pack(anchor="w",pady=(5,0))
        row=ttk.Frame(self.form);row.pack(fill="x",pady=(2,4))
        var=tk.DoubleVar(value=value);self.values[key]=var
        ttk.Scale(row,variable=var,from_=low,to=high).pack(side="left",fill="x",expand=True)
        ttk.Spinbox(row,textvariable=var,from_=low,to=high,increment=.1 if high<10 else 1,width=10).pack(side="right",padx=8)
        var.trace_add("write",self.schedule)

    def flag(self,key,label,value):
        var=tk.BooleanVar(value=value);self.values[key]=var
        ttk.Checkbutton(self.form,text=label,variable=var).pack(anchor="w",pady=6)
        var.trace_add("write",self.schedule)

    def seed(self,key,value):
        var=tk.StringVar(value=str(value));self.values[key]=var
        ttk.Label(self.form,text="随机种子（固定图案；改值重新生成）").pack(anchor="w",pady=6)
        ttk.Entry(self.form,textvariable=var).pack(fill="x");var.trace_add("write",self.schedule)

    def color(self,key,label,value):
        self.colors[key]=copy.deepcopy(value)
        def choose():
            initial="#%02x%02x%02x"%tuple(round(self.colors[key][k]*255) for k in ("red","green","blue"))
            chosen=colorchooser.askcolor(initial,parent=self.window)[1]
            if chosen:self.colors[key]=dict(zip(("red","green","blue"),(c/255 for c in ImageColor.getrgb(chosen))));self.schedule()
        ttk.Button(self.form,text=label+"…",command=choose).pack(fill="x",pady=8)

    def settings(self,key):return copy.deepcopy(default_adjustment(self.kind)[key]|(self.data.get(key) or {}))

    def hsv_settings(self):
        s=copy.deepcopy(self.data.get("hsvSettings") or dict(range="Master",colorize=self.data.get("colorize",False),invertRange=False,
                adjustments={"Master":{k:self.data.get(k,0) for k in ("hue","saturation","lightness")}},bands={}))
        for key in ("adjustments","bands"):
            if isinstance(s.get(key),list):s[key]=dict(zip(s[key][::2],s[key][1::2]))
        return s

    def build_form(self):
        self.clear_form();kind=self.kind
        if kind=="Exposure":
            s=self.settings("exposureSettings")
            for k,label,lo,hi in (("exposure","曝光 EV",-20,20),("offset","偏移",-.5,.5),("gamma","Gamma",.01,9.99)):self.slider(k,label,lo,hi,s[k])
        elif kind=="Levels":
            s=self.data["levels"]["ranges"][CHANNELS.index(self.selected)]
            for k,label,lo,hi in (("black","输入黑场",0,254),("white","输入白场",1,255),("gamma","中间调",.1,9.99),("outputBlack","输出黑场",0,255),("outputWhite","输出白场",0,255)):self.slider(k,label,lo,hi,s[k])
        elif kind=="Curves":
            self.points=self.data["curves"]["channels"][CHANNELS.index(self.selected)]
            ttk.Label(self.form,text="单击添加点，拖动调整；右键删除内部点。切换通道保留曲线。",wraplength=450).pack(anchor="w",pady=6)
            self.curve=tk.Canvas(self.form,width=288,height=288,bg="#191c23",highlightthickness=0);self.curve.pack()
            self.curve.bind("<Button-1>",self.curve_down);self.curve.bind("<B1-Motion>",self.curve_drag);self.curve.bind("<Button-3>",self.curve_delete)
            ttk.Button(self.form,text="重置当前通道",command=self.reset_curve).pack(pady=8);self.draw_curve()
        elif kind=="Hue/Saturation":
            s=self.hsv_settings();values=s["adjustments"].get(self.selected,{})
            for k,label,lo,hi in (("hue","色相角度",-180,360),("saturation","饱和度",-100,100),("lightness","明度",-100,100)):self.slider(k,label,lo,hi,values.get(k,0))
            self.flag("colorize","着色",s.get("colorize",False));self.flag("invertRange","反选当前色域",s.get("invertRange",False))
            if self.selected!="Master":
                band=s["bands"].get(self.selected) or dict(zip(("falloffStart","rangeStart","rangeEnd","falloffEnd"),BANDS[self.selected]))
                for k,label in (("falloffStart","渐入起点"),("rangeStart","色域起点"),("rangeEnd","色域终点"),("falloffEnd","渐出终点")):self.slider(k,label+"（角度）",0,360,band[k])
        elif kind=="Gradient Map":
            s=self.settings("gradientMapSettings");self.color("shadows","阴影颜色",s["shadows"]);self.color("highlights","高光颜色",s["highlights"]);self.flag("reversed","反向渐变",s["reversed"])
        elif kind=="Black & White":
            s=self.settings("blackWhiteSettings")
            for k,label in zip(("reds","yellows","greens","cyans","blues","magentas"),("红色","黄色","绿色","青色","蓝色","洋红")):self.slider(k,label,-200,300,s[k])
            self.flag("tint","着色",s["tint"]);self.slider("tintHue","着色色相",0,360,s["tintHue"]);self.slider("tintSaturation","着色饱和度",0,100,s["tintSaturation"])
        elif kind=="Color Balance":
            s=self.settings("colorBalanceSettings")
            for k,label in (("CyanRed","青色 ↔ 红色"),("MagentaGreen","洋红 ↔ 绿色"),("YellowBlue","黄色 ↔ 蓝色")):self.slider(k,label,-100,100,s[self.selected+k])
            self.flag("preserveLuminosity","保持亮度",s["preserveLuminosity"])
        elif kind=="Grain":
            s=self.settings("grainSettings")
            for k,label,lo,hi in (("amount","数量",0,100),("size","尺寸",.5,20),("roughness","粗糙度",0,100)):self.slider(k,label,lo,hi,s[k])
            self.seed("seed",s["seed"])
        elif kind=="Add Noise":
            self.slider("noiseAmount","数量",0,100,self.data.get("noiseAmount",10));self.flag("noiseGaussian","高斯分布",self.data.get("noiseGaussian",False))
            self.flag("noiseMonochromatic","单色",self.data.get("noiseMonochromatic",False));self.seed("noiseSeed",self.data.get("noiseSeed",0))
        elif kind=="Gaussian Blur":self.slider("radius","半径",.1,250,self.data.get("blurRadius",10))
        elif kind=="Motion Blur":self.slider("motionAngle","角度",-360,360,self.data.get("motionAngle",0));self.slider("motionDistance","距离",0,2000,self.data.get("motionDistance",10))
        else:ttk.Label(self.form,text="反相无需参数，可修改图层透明度与蒙版。").pack(pady=10)

    def store(self):
        data=copy.deepcopy(self.data);values={k:v.get() for k,v in self.values.items()};kind=self.kind
        for key in ("seed","noiseSeed"):
            if key in values:values[key]=int(values[key])
        if kind=="Exposure":data["exposureSettings"]=values
        elif kind=="Levels":data["levels"]["ranges"][CHANNELS.index(self.selected)].update(values);data["levels"]["channel"]=self.selected
        elif kind=="Curves":data["curves"]["channel"]=self.selected;data["curves"]["channels"][CHANNELS.index(self.selected)]=copy.deepcopy(self.points)
        elif kind=="Hue/Saturation":
            s=self.hsv_settings();s["range"]=self.selected;s["colorize"]=values["colorize"];s["invertRange"]=values["invertRange"]
            s["adjustments"][self.selected]={k:values[k] for k in ("hue","saturation","lightness")}
            if self.selected!="Master":s["bands"][self.selected]={k:values[k] for k in ("falloffStart","rangeStart","rangeEnd","falloffEnd")}
            data["hsvSettings"]=s
        elif kind=="Gradient Map":data["gradientMapSettings"]={**self.colors,**values}
        elif kind in ("Black & White","Grain"):
            key="blackWhiteSettings" if kind=="Black & White" else "grainSettings";data[key]=self.settings(key)|values
        elif kind=="Color Balance":
            s=self.settings("colorBalanceSettings");s.update({self.selected+k:v for k,v in values.items() if k!="preserveLuminosity"});s["preserveLuminosity"]=values["preserveLuminosity"];data["colorBalanceSettings"]=s
        elif kind=="Gaussian Blur":data["blurRadius"]=values["radius"]
        elif kind in ("Motion Blur","Add Noise"):data.update(values)
        validate_adjustment(data);self.data=data
        if kind=="Curves":self.points=self.data["curves"]["channels"][CHANNELS.index(self.selected)]

    def apply_preview(self):
        self.store();self.editor.preview_adjustment(self.layer_id,copy.deepcopy(self.data))

    def draw_curve(self):
        self.curve.delete("all")
        for x in (16,80,144,208,271):self.curve.create_line(x,16,x,271,fill="#353d4c");self.curve.create_line(16,x,271,x,fill="#353d4c")
        coords=[v for x in range(256) for v in (16+x,271-curve_value(self.points,x))];self.curve.create_line(*coords,fill="#67afff",width=2)
        for p in self.points:self.curve.create_oval(12+p["x"],267-p["y"],20+p["x"],275-p["y"],fill="white",outline="#67afff")

    def curve_down(self,event):
        x,y=max(0,min(255,event.x-16)),max(0,min(255,271-event.y))
        index=min(range(len(self.points)),key=lambda i:(self.points[i]["x"]-x)**2+(self.points[i]["y"]-y)**2)
        if (self.points[index]["x"]-x)**2+(self.points[index]["y"]-y)**2>100:
            if len(self.points)>=32:return
            if x in (0,255) or any(abs(p["x"]-x)<1 for p in self.points):return
            self.points.append(dict(x=x,y=y));self.points.sort(key=lambda p:p["x"]);index=next(i for i,p in enumerate(self.points) if p["x"]==x)
        self.drag_index=index;self.draw_curve();self.schedule()

    def curve_drag(self,event):
        if not hasattr(self,"drag_index"):return
        i=self.drag_index
        if 0<i<len(self.points)-1:self.points[i]["x"]=max(self.points[i-1]["x"]+1,min(self.points[i+1]["x"]-1,event.x-16))
        self.points[i]["y"]=max(0,min(255,271-event.y));self.draw_curve();self.schedule()

    def curve_delete(self,event):
        x,y=event.x-16,271-event.y
        for i,p in enumerate(self.points[1:-1],1):
            if (p["x"]-x)**2+(p["y"]-y)**2<=100:del self.points[i];self.draw_curve();self.schedule();break

    def reset_curve(self):
        self.points[:]=[dict(x=0,y=0),dict(x=255,y=255)];self.draw_curve();self.schedule()
