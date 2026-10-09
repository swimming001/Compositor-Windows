"""Create a small layered sample and app icon with code-generated geometry."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from engine import Document, save_project, export_image


def create_demo(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    document = Document(1100, 720)
    y = np.linspace(0, 1, 720)[:, None, None]
    top, bottom = np.array([22, 37, 64])[None, None, :], np.array([185, 107, 87])[None, None, :]
    sky = np.broadcast_to(np.uint8(top*(1-y)+bottom*y), (720, 1100, 3)).copy()
    document.add(Image.fromarray(sky).convert("RGBA"), "01 · 暮色天空")
    sun = Image.new("RGBA", (230, 230))
    ImageDraw.Draw(sun).ellipse((12, 12, 218, 218), fill="#f5bf75")
    layer = document.add(sun, "02 · 落日")
    layer.transform.x, layer.transform.y = 695, 175
    mountains = Image.new("RGBA", (1100, 400))
    draw = ImageDraw.Draw(mountains)
    draw.polygon([(0, 170), (160, 40), (340, 200), (530, 65), (715, 175), (960, 25), (1100, 160), (1100, 400), (0, 400)], fill="#283e59")
    layer = document.add(mountains, "03 · 远山")
    layer.transform.y = 320
    near = Image.new("RGBA", (1100, 280))
    ImageDraw.Draw(near).polygon([(0, 50), (240, 165), (420, 90), (650, 170), (820, 80), (1100, 145), (1100, 280), (0, 280)], fill="#14263c")
    layer = document.add(near, "04 · 前景")
    layer.transform.y = 440
    text = Image.new("RGBA", (620, 135))
    draw = ImageDraw.Draw(text)
    font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 56)
    small = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 18)
    draw.text((0, 0), "MAKE ROOM", font=font, fill="#f6e6cf")
    draw.text((3, 87), "FOR YOUR NEXT IDEA     /     WINDOWS EDITION", font=small, fill="#debeaa")
    layer = document.add(text, "05 · 标题（栅格）")
    layer.transform.x, layer.transform.y = 75, 65
    document.active_id = layer.id
    save_project(document, folder/"落日合成示例.comp")
    export_image(document, folder/"落日合成示例.png")
    icon = Image.new("RGBA", (256, 256), "#192233")
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((26, 26, 230, 230), radius=40, fill="#37699a")
    draw.rounded_rectangle((60, 68, 188, 196), radius=16, fill="#8ccaff")
    draw.rounded_rectangle((85, 43, 213, 171), radius=16, fill="#eef6ff")
    icon.save(folder.parent/"app.ico", sizes=[(16, 16), (32, 32), (48, 48), (128, 128), (256, 256)])


if __name__ == "__main__":
    create_demo(Path(__file__).parent/"examples")
