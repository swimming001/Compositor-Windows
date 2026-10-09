"""Keep the old sample; add a separate v0.2 editable-text/adjustment example."""
from pathlib import Path
from PIL import ImageColor

from create_demo import create_demo
from engine import load_project, save_project, export_image, Layer, Transform
from adjustments import default_adjustment
from dialogs import text_image


ROOT = Path(__file__).parent


def add_text(document, content, x, y, size, color, name):
    rgb = ImageColor.getrgb(color)
    style = dict(content=content, fontName="SegoeUI", fontSize=size, red=rgb[0]/255, green=rgb[1]/255, blue=rgb[2]/255,
                 alignment="Left", tracking=0, leading=0)
    image = text_image(style)
    layer = document.add(image, name)
    layer.transform.x, layer.transform.y = x, y
    layer.metadata["text"] = style
    return layer


def main():
    generated = ROOT/"verification"/"v0.2-generated"
    create_demo(generated)
    document = load_project(generated/"落日合成示例.comp")
    document.layers.pop()  # Replace the generated raster title, never a user's file.
    title = add_text(document, "CREATE FREELY", 55, 50, 54, "#f6e6cf", "05 · 双击编辑标题")
    add_text(document, "WINDOWS 0.2  /  LAYERS, TYPE & ADJUSTMENTS", 59, 125, 16, "#debeaa", "06 · 可编辑副标题")
    settings = default_adjustment("Exposure")
    settings["exposureSettings"]["exposure"] = .15
    document.layers.append(Layer("07 · 双击调整曝光", None, Transform(0, 0, document.width, document.height), metadata={"adjustment": settings}))
    document.active_id = title.id
    save_project(document, ROOT/"examples"/"可编辑海报0.2.comp")
    export_image(document, ROOT/"examples"/"可编辑海报0.2.png")


if __name__ == "__main__": main()
