"""Generate the redistributable, synthetic layered PSD validation fixture."""
from PIL import Image


def layered_fixture(path):
    from psd_tools import PSDImage
    from psd_tools.constants import BlendMode
    psd=PSDImage.new("RGB",(64,48),color=(0,0,0))
    psd.create_pixel_layer(Image.new("RGB",(64,48),(40,50,60)),name="Background")
    group=psd.create_group(name="Editable folder",blend_mode=BlendMode.NORMAL,opacity=180)
    layer=group.create_pixel_layer(Image.new("RGB",(20,18),(100,150,200)),name="Masked pixels",top=9,left=11)
    mask=Image.new("L",(20,18),255);mask.putpixel((0,0),0)
    layer.create_mask(mask);layer.opacity=190
    hidden=psd.create_pixel_layer(Image.new("RGB",(10,10),(255,0,0)),name="Hidden",top=4,left=5)
    hidden.visible=False
    psd.save(path)
    return path
