"""Remaining upstream adjustment models, using straight alpha and document coordinates."""
import math
import numpy as np
from PIL import Image

RANGES=("Master","Reds","Yellows","Greens","Cyans","Blues","Magentas")
BANDS=dict(zip(RANGES[1:],[(315,345,15,45),(15,45,75,105),(75,105,135,165),(135,165,195,225),(195,225,255,285),(255,285,315,345)]))


def defaults(data,key):
    from adjustments import default_adjustment
    return default_adjustment(data["kind"])[key] | (data.get(key) or {})


def validate_extended(data):
    from adjustments import number
    def flag(value):
        if type(value) is not bool: raise ValueError("调整层布尔参数无效。")
    def seed(value):
        if type(value) is not int or not 0<=value<=0xffffffff: raise ValueError("随机种子范围为 0–4294967295。")
    kind=data["kind"]
    if kind=="Hue/Saturation":
        s=data.get("hsvSettings") or dict(colorize=data.get("colorize",False),adjustments={"Master":{k:data.get(k,0) for k in ("hue","saturation","lightness")}})
        flag(s.get("colorize",False));flag(s.get("invertRange",False))
        if s.get("range","Master") not in RANGES: raise ValueError("无效色相范围。")
        adjustments=s.get("adjustments",{})
        if isinstance(adjustments,list): adjustments=dict(zip(adjustments[::2],adjustments[1::2]))
        if not isinstance(adjustments,dict) or set(adjustments)-set(RANGES): raise ValueError("无效色相范围。")
        for value in adjustments.values():
            number(value.get("hue",0),-180,360);number(value.get("saturation",0),-100,100);number(value.get("lightness",0),-100,100)
        bands=s.get("bands",{})
        if isinstance(bands,list): bands=dict(zip(bands[::2],bands[1::2]))
        if not isinstance(bands,dict) or set(bands)-set(RANGES): raise ValueError("无效色相范围。")
        for value in bands.values():
            for key in ("falloffStart","rangeStart","rangeEnd","falloffEnd"):number(value[key],0,360)
    elif kind=="Gradient Map":
        s=defaults(data,"gradientMapSettings");flag(s["reversed"])
        for key in ("shadows","highlights"):
            for c in ("red","green","blue"):number(s[key][c],0,1)
    elif kind=="Black & White":
        s=defaults(data,"blackWhiteSettings");flag(s["tint"])
        for key in ("reds","yellows","greens","cyans","blues","magentas"):number(s[key],-200,300)
        number(s["tintHue"],0,360);number(s["tintSaturation"],0,100)
    elif kind=="Color Balance":
        s=defaults(data,"colorBalanceSettings");flag(s["preserveLuminosity"])
        for key,value in s.items():
            if key!="preserveLuminosity": number(value,-100,100)
    elif kind=="Grain":
        s=defaults(data,"grainSettings");number(s["amount"],0,100);number(s["size"],.5,20);number(s["roughness"],0,100);seed(s["seed"])
    elif kind=="Add Noise":
        number(data.get("noiseAmount",10),0,100);flag(data.get("noiseGaussian",False));flag(data.get("noiseMonochromatic",False));seed(data.get("noiseSeed",0))
    elif kind=="Motion Blur": number(data.get("motionAngle",0),-360,360);number(data.get("motionDistance",10),0,2000)


def hsl(rgb):
    high,low=rgb.max(-1),rgb.min(-1);delta=high-low;light=(high+low)/2
    sat=np.divide(delta,1-np.abs(2*light-1),out=np.zeros_like(delta),where=delta>0)
    safe=np.maximum(delta,1e-8)
    r,g,b=rgb[...,0],rgb[...,1],rgb[...,2]
    hue=np.where(high==r,(g-b)/safe,np.where(high==g,(b-r)/safe+2,(r-g)/safe+4))*60
    return hue%360,sat,light


def rgb_from_hsl(hue,sat,light):
    sector=(hue%360)/60;chroma=(1-np.abs(2*light-1))*sat
    second=chroma*(1-np.abs(sector%2-1));zero=np.zeros_like(chroma);base=light-chroma/2
    channels=[np.select([sector<1,sector<2,sector<3,sector<4,sector<5],[chroma,second,zero,zero,second],default=chroma),
              np.select([sector<1,sector<2,sector<3,sector<4,sector<5],[second,chroma,chroma,second,zero],default=zero),
              np.select([sector<1,sector<2,sector<3,sector<4,sector<5],[zero,zero,second,chroma,chroma],default=second)]
    return np.stack([c+base for c in channels],axis=-1)


def hash32(value):
    x=np.asarray(value,dtype=np.uint32)
    with np.errstate(over="ignore"):
        x=(x^(x>>16))*np.uint32(0x7feb352d);x=(x^(x>>15))*np.uint32(0x846ca68b)
    return x^(x>>16)


def grain_field(u,v,size,seed):
    x,y=np.floor(u/size).astype(np.int64),np.floor(v/size).astype(np.int64)
    tx,ty=u/size-x,v/size-y;tx=tx*tx*(3-2*tx);ty=ty*ty*(3-2*ty)
    def lattice(x,y):
        with np.errstate(over="ignore"):
            value=hash32(x.astype(np.uint32)*np.uint32(0x9e3779b1)^hash32(y.astype(np.uint32)*np.uint32(0x85ebca77)^np.uint32(seed)))
        return (value&0xffff)/65535.+(value>>16)/65535.-1
    a,b,c,d=lattice(x,y),lattice(x+1,y),lattice(x,y+1),lattice(x+1,y+1)
    return ((a+(b-a)*tx)+(c+(d-c)*tx-a-(b-a)*tx)*ty)*1.6


def apply_extended(image,data,scale,origin):
    kind=data["kind"]
    if kind=="Motion Blur":
        distance=data.get("motionDistance",10)*scale
        if distance==0:return image
        count=max(2,min(128,math.ceil(distance)*2+1));angle=math.radians(data.get("motionAngle",0))
        source=image.convert("RGBa");total=np.zeros((image.height,image.width,4),dtype=np.float32)
        for shift in np.linspace(-distance/2,distance/2,count):
            sample=source.transform(source.size,Image.Transform.AFFINE,(1,0,shift*math.cos(angle),0,1,-shift*math.sin(angle)),Image.Resampling.BILINEAR)
            total+=np.asarray(sample)
        return Image.frombytes("RGBa",image.size,np.uint8(np.clip(total/count+.5,0,255)).tobytes()).convert("RGBA")
    pixels=np.asarray(image,dtype=np.float32)/255;rgb=pixels[...,:3];alpha=image.getchannel("A")
    if kind=="Hue/Saturation":
        s=data.get("hsvSettings") or dict(colorize=data.get("colorize",False),adjustments={"Master":{k:data.get(k,0) for k in ("hue","saturation","lightness")}})
        hue,sat,light=hsl(rgb);response=np.zeros((*hue.shape,3),dtype=np.float32)
        ranges=s.get("adjustments",{})
        if isinstance(ranges,list):ranges=dict(zip(ranges[::2],ranges[1::2]))
        bands=s.get("bands",{})
        if isinstance(bands,list):bands=dict(zip(bands[::2],bands[1::2]))
        for name,value in ranges.items():
            weight=1
            if name!="Master":
                band=bands.get(name);start,rs,re,end=[band[k] for k in ("falloffStart","rangeStart","rangeEnd","falloffEnd")] if band else BANDS[name]
                span=(end-start)%360;position=(hue-start)%360;rin=(rs-start)%360;plateau=(re-start)%360
                weight=np.ones_like(hue) if span==0 else np.where(position>span,0,np.where(position<rin,position/max(rin,1e-8),np.where(position<=plateau,1,(span-position)/max(span-plateau,1e-8))))
                if s.get("invertRange") and name==s.get("range","Master"):weight=1-weight
            response+=np.stack([np.broadcast_to(value.get(k,0)*weight,hue.shape) for k in ("hue","saturation","lightness")],axis=-1)
        if s.get("colorize"):
            master=ranges.get(s.get("range","Master"),{});hue=np.full_like(hue,master.get("hue",0));sat=np.full_like(sat,max(0,master.get("saturation",25))/100)
            response[...,2]=master.get("lightness",0)
        else:
            hue=(hue+response[...,0])%360;amount=np.clip(response[...,1]/100,-1,1)
            sat=np.where(amount<=0,sat*(1+amount),np.where(amount>=1,np.where(sat>0,1,0),np.minimum(1,sat/np.maximum(1-amount,1e-8))))
        amount=np.clip(response[...,2]/100,-1,1);light=np.where(amount>=0,light+(1-light)*amount,light*(1+amount));rgb=rgb_from_hsl(hue,sat,light)
    elif kind=="Gradient Map":
        s=defaults(data,"gradientMapSettings");dark,bright=s["shadows"],s["highlights"]
        if s["reversed"]:dark,bright=bright,dark
        lum=np.round(rgb@np.float32([.2126,.7152,.0722])*255)/255
        a,b=[np.float32([c[k] for k in ("red","green","blue")]) for c in (dark,bright)]
        rgb=a+(b-a)*lum[...,None]
    elif kind=="Black & White":
        s=defaults(data,"blackWhiteSettings");r,g,b=rgb[...,0],rgb[...,1],rgb[...,2]
        high,low=rgb.max(-1),rgb.min(-1);mid=rgb.sum(-1)-high-low
        primary=np.where(high==r,s["reds"],np.where(high==g,s["greens"],s["blues"]))
        secondary=np.where(high==r,np.where(g>=b,s["yellows"],s["magentas"]),np.where(high==g,np.where(r>=b,s["yellows"],s["cyans"]),np.where(g>=r,s["cyans"],s["magentas"])))
        gray=np.clip(low+(mid-low)*secondary/100+(high-mid)*primary/100,0,1)
        rgb=rgb_from_hsl(np.full_like(gray,s["tintHue"]),np.full_like(gray,s["tintSaturation"]/100),gray) if s["tint"] else np.repeat(gray[...,None],3,-1)
    elif kind=="Color Balance":
        s=defaults(data,"colorBalanceSettings");before=rgb@np.float32([.299,.587,.114])
        shadow=np.clip((rgb-.333)/-.25+.5,0,1)*.7;highlight=np.clip((rgb+.333-1)/.25+.5,0,1)*.7
        mid=np.clip((rgb-.333)/.25+.5,0,1)*np.clip((rgb+.333-1)/-.25+.5,0,1)*.7
        vectors=[np.float32([s[tone+k]/100 for k in ("CyanRed","MagentaGreen","YellowBlue")]) for tone in ("shadow","mid","highlight")]
        rgb=np.clip(rgb+shadow*vectors[0]+mid*vectors[1]+highlight*vectors[2],0,1)
        if s["preserveLuminosity"]:
            after=rgb@np.float32([.299,.587,.114]);rgb*=np.divide(before,after,out=np.ones_like(before),where=after>.0001)[...,None]
    elif kind in ("Grain","Add Noise"):
        u=origin[0]+(np.arange(image.width)[None,:]+.5)/scale;v=origin[1]+(np.arange(image.height)[:,None]+.5)/scale
        if kind=="Grain":
            s=defaults(data,"grainSettings");smooth=grain_field(u,v,s["size"],s["seed"]);fine=grain_field(u,v,max(.5,s["size"]*.35),int(hash32(s["seed"]^0xa511e9b3)))
            noise=smooth+(fine-smooth)*s["roughness"]/100;lum=rgb@np.float32([.2126,.7152,.0722])
            rgb=rgb+(noise*s["amount"]/100*.35*(.4+2.4*lum*(1-lum)))[...,None]
        else:
            with np.errstate(over="ignore"):
                base=hash32(np.uint32(data.get("noiseSeed",0))^hash32(np.floor(u).astype(np.uint32)*np.uint32(0x9e3779b9)^hash32(np.floor(v).astype(np.uint32)*np.uint32(0x85ebca6b))))
                noise=[]
                for c in range(3):
                    key=base if data.get("noiseMonochromatic",False) else base+np.uint32(c*0x9e3779b9&0xffffffff)
                    first=(hash32(key)>>8)/16777216.
                    values=np.sqrt(-2*np.log(np.maximum(1-first,1e-8)))*np.cos(2*np.pi*(hash32(key^np.uint32(0x68e31da4))>>8)/16777216)*2/3 if data.get("noiseGaussian",False) else first*2-1
                    noise.append(values)
            rgb=rgb+np.stack(noise,-1)*data.get("noiseAmount",10)/100*.5
    else:raise ValueError("未知调整类型。")
    result=Image.fromarray(np.uint8(np.clip(rgb*255+.5,0,255))).convert("RGBA");result.putalpha(alpha)
    return result
