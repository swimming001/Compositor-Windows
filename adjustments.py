"""Portable implementations of upstream exposure, levels and Hermite curves."""
import math
from PIL import Image, ImageFilter, ImageOps


KINDS = ("Exposure", "Levels", "Curves", "Hue/Saturation", "Gradient Map", "Black & White", "Color Balance",
         "Grain", "Add Noise", "Gaussian Blur", "Motion Blur", "Invert")
LABELS = dict(zip(KINDS,("曝光","色阶","曲线","色相/饱和度","渐变映射","黑白","色彩平衡","颗粒","添加杂色","高斯模糊","动感模糊","反相")))


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("调整层参数无效。")
    return value


def default_adjustment(kind):
    if kind not in KINDS: raise ValueError("此调整层尚未移植。")
    return {"kind": kind, "hue": 0, "saturation": 0, "lightness": 0, "colorize": False,
            "levels": {"channel": "RGB", "ranges": [dict(black=0, white=255, gamma=1, outputBlack=0, outputWhite=255) for _ in range(4)]},
            "curves": {"channel": "RGB", "channels": [[dict(x=0, y=0), dict(x=255, y=255)] for _ in range(4)]},
            "exposureSettings": {"exposure": 0, "offset": 0, "gamma": 1}, "blurRadius": 10,
            "gradientMapSettings":dict(shadows=dict(red=0,green=0,blue=0),highlights=dict(red=1,green=1,blue=1),reversed=False),
            "blackWhiteSettings":dict(reds=40,yellows=60,greens=40,cyans=60,blues=20,magentas=80,tint=False,tintHue=40,tintSaturation=20),
            "colorBalanceSettings":{**{tone+color:0 for tone in ("shadow","mid","highlight") for color in ("CyanRed","MagentaGreen","YellowBlue")},"preserveLuminosity":True},
            "grainSettings":dict(amount=25,size=1.5,roughness=50,seed=0),"motionAngle":0,"motionDistance":10,
            "noiseAmount":10,"noiseGaussian":False,"noiseMonochromatic":False,"noiseSeed":0}


def validate_adjustment(data):
    if not isinstance(data, dict) or data.get("kind") not in KINDS:
        raise ValueError("此调整层尚未移植。")
    allowed = {"kind", "hue", "saturation", "lightness", "colorize", "hsvSettings", "levels", "curves",
               "exposureSettings", "gradientMapSettings", "grainSettings", "blackWhiteSettings", "colorBalanceSettings",
               "blurRadius", "motionAngle", "motionDistance", "noiseAmount", "noiseGaussian", "noiseMonochromatic", "noiseSeed"}
    if set(data)-allowed: raise ValueError("调整层包含未知参数。")
    kind = data["kind"]
    if kind in ("Hue/Saturation","Gradient Map","Black & White","Color Balance","Grain","Add Noise","Motion Blur"):
        from color_adjustments import validate_extended
        validate_extended(data)
    if kind == "Exposure":
        settings = data.get("exposureSettings") or {}
        number(settings.get("exposure", 0), -20, 20)
        number(settings.get("offset", 0), -.5, .5)
        number(settings.get("gamma", 1), .01, 9.99)
    elif kind == "Gaussian Blur":
        number(data.get("blurRadius", 10), .1, 250)
    elif kind == "Levels":
        ranges = data.get("levels", {}).get("ranges")
        if not isinstance(ranges, list) or len(ranges) != 4: raise ValueError("色阶必须包含四个通道。")
        for settings in ranges:
            black = number(settings["black"], 0, 254)
            number(settings["white"], black+1, 255)
            number(settings["gamma"], .1, 9.99)
            number(settings["outputBlack"], 0, 255)
            number(settings["outputWhite"], 0, 255)
    elif kind == "Curves":
        channels = data.get("curves", {}).get("channels")
        if not isinstance(channels, list) or len(channels) != 4: raise ValueError("曲线必须包含四个通道。")
        for points in channels:
            if not 2 <= len(points) <= 32 or points[0]["x"] != 0 or points[-1]["x"] != 255:
                raise ValueError("曲线端点和数量无效。")
            previous = -1
            for point in points:
                x = number(point["x"], 0, 255)
                number(point["y"], 0, 255)
                if x <= previous: raise ValueError("曲线控制点必须按 X 递增。")
                previous = x


def curve_value(points, x):
    index = next((i for i in range(len(points)-1) if x < points[i+1]["x"]), len(points)-2)
    slopes = [(b["y"]-a["y"])/(b["x"]-a["x"]) for a, b in zip(points, points[1:])]
    def slope(j):
        if j == 0: return slopes[0]
        if j == len(points)-1: return slopes[-1]
        return 0 if slopes[j-1]*slopes[j] <= 0 else 2/(1/slopes[j-1]+1/slopes[j])
    p, q = points[index:index+2]
    h = q["x"]-p["x"]
    t = min(1, max(0, (x-p["x"])/h))
    return min(255, max(0, (2*t**3-3*t*t+1)*p["y"]+(t**3-2*t*t+t)*h*slope(index)
                       +(-2*t**3+3*t*t)*q["y"]+(t**3-t*t)*h*slope(index+1)))


def apply_adjustment(image, data, scale=1, origin=(0,0)):
    validate_adjustment(data)
    kind = data["kind"]
    if kind in ("Hue/Saturation","Gradient Map","Black & White","Color Balance","Grain","Add Noise","Motion Blur"):
        from color_adjustments import apply_extended
        return apply_extended(image,data,scale,origin)
    if kind == "Gaussian Blur":
        return image.convert("RGBa").filter(ImageFilter.GaussianBlur(data.get("blurRadius", 10)*scale)).convert("RGBA")
    if kind == "Invert":
        rgb = ImageOps.invert(image.convert("RGB")).convert("RGBA")
        rgb.putalpha(image.getchannel("A"))
        return rgb
    def level(settings, x):
        value = min(1, max(0, (x-settings["black"])/(settings["white"]-settings["black"])))**(1/settings["gamma"])
        return settings["outputBlack"]+value*(settings["outputWhite"]-settings["outputBlack"])
    tables = []
    for channel in range(1, 4):
        table = []
        for x in range(256):
            if kind == "Exposure":
                settings = data.get("exposureSettings") or {}
                encoded = x/255
                linear = encoded/12.92 if encoded <= .04045 else ((encoded+.055)/1.055)**2.4
                linear = max(0, linear*2**settings.get("exposure", 0)+settings.get("offset", 0))**(1/settings.get("gamma", 1))
                value = (linear*12.92 if linear <= .0031308 else 1.055*linear**(1/2.4)-.055)*255
            elif kind == "Levels":
                ranges = data["levels"]["ranges"]
                value = level(ranges[0], level(ranges[channel], x))
            else:
                curves = data["curves"]["channels"]
                value = curve_value(curves[0], curve_value(curves[channel], x))
            table.append(round(min(255, max(0, value))))
        tables.append(table)
    tables.append(list(range(256)))
    return image.point([value for table in tables for value in table])
