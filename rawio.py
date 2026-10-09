"""LibRaw decoding into the editor's sRGB raster working space."""
from pathlib import Path
import math
import os
import struct
import tempfile
import numpy as np
from PIL import Image

RAW_EXTENSIONS = (".raw", ".dng", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2",
                  ".raf", ".orf", ".rw2", ".pef", ".ptx", ".kdc", ".mos", ".erf", ".3fr", ".iiq")


def decode_raw(path, exposure=0, white_balance="camera", half_size=False,output_bits=8):
    import rawpy
    from engine import dimensions
    if not math.isfinite(exposure) or not -8 <= exposure <= 8: raise ValueError("RAW 曝光范围为 -8 至 8 EV。")
    if white_balance not in ("camera", "auto", "daylight"): raise ValueError("无效 RAW 白平衡。")
    if output_bits not in (8,16):raise ValueError("RAW 输出位深为 8 或 16。")
    with rawpy.imread(str(path)) as raw:
        # Reject dimensions before allocating demosaic output.
        w, h = raw.sizes.width, raw.sizes.height
        dimensions(max(1, w//2) if half_size else w, max(1, h//2) if half_size else h)
        rgb = raw.postprocess(use_camera_wb=white_balance == "camera", use_auto_wb=white_balance == "auto",
                              half_size=half_size, output_color=rawpy.ColorSpace.sRGB, output_bps=output_bits,
                              no_auto_bright=True, bright=2**exposure)
    dimensions(rgb.shape[1],rgb.shape[0])
    return rgb


def develop_raw(path,exposure=0,white_balance="camera",half_size=False):
    return Image.fromarray(decode_raw(path,exposure,white_balance,half_size,8)).convert("RGBA")


def export_raw_tiff(source,target,exposure=0,white_balance="camera",half_size=False):
    """Develop the original RAW straight to RGB16 TIFF, bypassing the 8-bit editor."""
    from PIL import ImageCms
    target=Path(target)
    if target.suffix.lower() not in (".tif",".tiff"):raise ValueError("16 位 RAW 显影导出请使用 .tif / .tiff。")
    if Path(source).resolve()==target.resolve():raise ValueError("RAW 原文件不能作为导出目标。")
    rgb=decode_raw(source,exposure,white_balance,half_size,16).astype("<u2",copy=False)
    height,width,_=rgb.shape
    profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    # Baseline little-endian TIFF with one interleaved RGB16 strip and an ICC profile.
    count=14;extra=8+2+count*12+4;bits=extra;sample=bits+6;xres=sample+6;yres=xres+8;icc=yres+8;pixels=(icc+len(profile)+1)//2*2
    tags=[(256,4,1,width),(257,4,1,height),(258,3,3,bits),(259,3,1,1),(262,3,1,2),
          (273,4,1,pixels),(277,3,1,3),(278,4,1,height),(279,4,1,rgb.nbytes),
          (282,5,1,xres),(283,5,1,yres),(284,3,1,1),(296,3,1,2),(34675,7,len(profile),icc)]
    # SampleFormat defaults to unsigned integer; its reserved values are unnecessary.
    header=bytearray(b"II"+struct.pack("<HIH",42,8,count))
    for tag,kind,length,value in sorted(tags):
        payload=struct.pack("H",value)+b"\x00\x00" if kind==3 and length==1 else struct.pack("I",value)
        header.extend(struct.pack("HHI",tag,kind,length)+payload)
    header.extend(struct.pack("I",0));header.extend(struct.pack("3H",16,16,16));header.extend(struct.pack("3H",1,1,1))
    header.extend(struct.pack("4I",72,1,72,1));header.extend(profile);header.extend(b"\x00"*(pixels-len(header)))
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix=".compositor-raw-",suffix=".tif",dir=target.parent)
    try:
        with os.fdopen(fd,"wb") as stream:
            stream.write(header)
            for y in range(0,height,128):stream.write(rgb[y:y+128].tobytes())
        with Image.open(temporary) as checked:
            if checked.size!=(width,height) or tuple(checked.tag_v2[258])!=(16,16,16):raise ValueError("16 位 TIFF 校验失败，目标文件未替换。")
        os.replace(temporary,target)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    return width,height
