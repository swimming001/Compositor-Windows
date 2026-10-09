"""Upstream text model: UTF-16 rich runs, tracking, leading and paragraph boxes."""
import copy
import difflib
import functools
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONT_FILES = {"MicrosoftYaHei": "msyh.ttc", "ArialMT": "arial.ttf", "Arial": "arial.ttf", "Helvetica": "arial.ttf",
              "SegoeUI": "segoeui.ttf", "TimesNewRomanPSMT": "times.ttf", "Consolas": "consola.ttf"}


@functools.lru_cache(maxsize=1)
def installed_fonts():
    result = dict(FONT_FILES)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as key:
            for index in range(winreg.QueryInfoKey(key)[1]):
                name, filename, _ = winreg.EnumValue(key, index)
                name = name.replace(" (TrueType)", "").replace(" (OpenType)", "")
                result[name] = filename
    except (ImportError, OSError): pass
    return result


@functools.lru_cache(maxsize=192)
def font(name, size):
    choices = installed_fonts()
    filename = choices.get(name)
    if filename is None:
        normalized = name.lower().replace("-", "").replace(" ", "").replace("psmt", "").replace("mt", "")
        filename = next((v for k,v in choices.items() if k.lower().replace(" ", "").replace("-", "") == normalized), "msyh.ttc")
    path = Path(filename)
    if not path.is_absolute(): path = Path("C:/Windows/Fonts")/path
    return ImageFont.truetype(str(path), size)


def utf16_length(text): return len(text.encode("utf-16-le"))//2


def validate_style(style):
    content = style.get("content", "")
    if not isinstance(content, str) or utf16_length(content) > 100000: raise ValueError("文字最多支持 10 万个 UTF-16 单元。")
    for key, lo, hi, default in (("fontSize",1,2000,72), ("tracking",-100,1000,0), ("leading",0,5000,0), ("red",0,1,0), ("green",0,1,0), ("blue",0,1,0)):
        value = style.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or not lo <= value <= hi: raise ValueError("文字排版参数超出范围。")
    if style.get("alignment", "Left") not in ("Left","Center","Right"): raise ValueError("无效文字对齐。")
    size = style.get("boxSize")
    if size is not None:
        if not isinstance(size,list) or len(size)!=2 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<16 or v>12000 for v in size): raise ValueError("文本框尺寸为 16–12000 像素。")
        from engine import dimensions
        dimensions(round(size[0]), round(size[1]))
    count = utf16_length(content)
    for key in ("colorRuns", "fontRuns"):
        end = 0
        for run in style.get(key) or []:
            start, length = run["location"], run["length"]
            if type(start) is not int or type(length) is not int or start<end or length<1 or start+length>count: raise ValueError("局部文字样式范围无效。")
            end = start+length
            if key=="fontRuns" and (not isinstance(run.get("fontName"),str) or not run["fontName"] or len(run["fontName"])>200): raise ValueError("局部字体名称无效。")
            if key=="colorRuns" and any(not isinstance(run.get(c),(int,float)) or not math.isfinite(run[c]) or not 0<=run[c]<=1 for c in ("red","green","blue")): raise ValueError("局部文字颜色无效。")


def unit_styles(style, key):
    base = style.get("fontName","MicrosoftYaHei") if key=="fontRuns" else tuple(style.get(k,0) for k in ("red","green","blue"))
    values = [base]*utf16_length(style.get("content", ""))
    for run in style.get(key) or []:
        value = run["fontName"] if key=="fontRuns" else tuple(run[k] for k in ("red","green","blue"))
        values[run["location"]:run["location"]+run["length"]] = [value]*run["length"]
    return base, values


def write_runs(style, key, base, values):
    runs = []
    for index, value in enumerate(values):
        if value == base: continue
        payload = {"fontName": value} if key=="fontRuns" else dict(zip(("red","green","blue"),value))
        if runs and runs[-1]["location"]+runs[-1]["length"] == index and all(runs[-1][k]==v for k,v in payload.items()): runs[-1]["length"] += 1
        else: runs.append(dict(location=index,length=1,**payload))
    if runs: style[key] = runs
    else: style.pop(key,None)


def replace_content(style, content):
    old = style.get("content", "")
    if old == content: return copy.deepcopy(style)
    result = copy.deepcopy(style)
    operations = difflib.SequenceMatcher(a=old,b=content,autojunk=False).get_opcodes()
    for key in ("colorRuns","fontRuns"):
        base, values = unit_styles(style,key)
        new = []
        for tag,i,j,k,l in operations:
            start,end = utf16_length(old[:i]),utf16_length(old[:j])
            if tag=="equal": new += values[start:end]
            elif tag in ("insert","replace"):
                inherited = values[start-1] if start else values[0] if values else base
                new += [inherited]*utf16_length(content[k:l])
        write_runs(result,key,base,new)
    result["content"] = content
    return result


def set_range(style, key, start, end, value):
    base, values = unit_styles(style,key)
    start,end = max(0,min(start,len(values))),max(0,min(end,len(values)))
    if end<=start or (start==0 and end==len(values)):
        if key=="fontRuns": style["fontName"] = value
        else: style.update(zip(("red","green","blue"),value))
        style.pop(key,None)
    else:
        values[start:end] = [value]*(end-start)
        write_runs(style,key,base,values)


def text_image(style):
    validate_style(style)
    from engine import dimensions
    content = style.get("content", "")
    base_font, fonts = unit_styles(style,"fontRuns")
    base_color, colors = unit_styles(style,"colorRuns")
    size = round(style.get("fontSize",72))
    tracking = style.get("tracking",0)
    lineheight = style.get("leading",0) or size*1.2
    box = style.get("boxSize")
    available = box[0]-24 if box else None
    lines, current, width, offset = [], [], 0, 0
    # Each record stores its original UTF-16 style; wrapping never loses run offsets.
    for char in content:
        name,color = fonts[offset] if fonts else base_font, colors[offset] if colors else base_color
        offset += utf16_length(char)
        face = font(name,size)
        advance = max(.1,float(face.getlength(char))+tracking)
        if char=="\n": lines.append((current,width)); current,width=[],0; continue
        if available is not None and current and width+advance>available:
            # Prefer a word boundary; CJK naturally falls back to character wrapping.
            space = next((i for i in range(len(current)-1,-1,-1) if current[i][0].isspace()),None)
            if space is not None and space>0:
                head,tail = current[:space],current[space+1:]
                lines.append((head,sum(r[3] for r in head)))
                current,width = tail,sum(r[3] for r in tail)
            else: lines.append((current,width)); current,width=[],0
        current.append((char,name,color,advance)); width += advance
    lines.append((current,width))
    # Align using the same shaped runs that will actually be drawn. Individual
    # glyph widths overestimate kerned text such as "AV" and shift its anchor.
    def drawn_length(records):
        if tracking: return sum(r[3] for r in records)-tracking if records else 0
        total,index=0,0
        while index<len(records):
            end=index+1
            while end<len(records) and records[end][1:3]==records[index][1:3]:end+=1
            total+=float(font(records[index][1],size).getlength("".join(r[0] for r in records[index:end])))
            index=end
        return total
    lines=[(records,drawn_length(records)) for records,_ in lines]
    width = round(box[0]) if box else max(25,math.ceil(max(w for _,w in lines))+24)
    max_ascent,max_descent = max(font(n,size).getmetrics()[0] for n in {base_font,*fonts}),max(font(n,size).getmetrics()[1] for n in {base_font,*fonts})
    height = round(box[1]) if box else max(25,math.ceil((len(lines)-1)*lineheight+max_ascent+max_descent)+24)
    dimensions(width,height)
    image = Image.new("RGBA",(width,height))
    pen = ImageDraw.Draw(image)
    for number,(records,length) in enumerate(lines):
        baseline = 12+max_ascent+number*lineheight
        if baseline-max_ascent>=height-12: break
        alignment = style.get("alignment","Left")
        x = 12 if alignment=="Left" else (width-length)/2 if alignment=="Center" else width-12-length
        index = 0
        while index<len(records):
            char,name,color,advance = records[index]
            end = index+1
            # Shape complete same-style runs (Pillow uses RAQM when available).
            if tracking==0:
                while end<len(records) and records[end][1:3]==records[index][1:3]: end += 1
            text = "".join(r[0] for r in records[index:end])
            face = font(name,size)
            pen.text((x,baseline),text,font=face,anchor="ls",fill=tuple(round(c*255) for c in color))
            x += float(face.getlength(text)) if tracking==0 else advance
            index = end
    # Paragraph frames clip overflowing glyphs to the padded content box.
    if box:
        clipped = Image.new("RGBA",image.size)
        clipped.paste(image.crop((12,12,width-12,height-12)),(12,12))
        image = clipped
    return image
