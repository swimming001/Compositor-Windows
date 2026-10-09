"""Generate a synthetic editable example and README preview without external photos."""
from pathlib import Path
from engine import load_project,save_project,export_image,Layer,Transform
from adjustments import default_adjustment
from typography import text_image

ROOT=Path(__file__).resolve().parent


def create():
    previous=ROOT/"examples/创作工作台0.3.comp"
    if not previous.exists():
        from create_demo_v03 import create as create_previous
        create_previous()
    d=load_project(previous)
    subtitle=next(layer for layer in d.layers if layer.name=="副标题")
    subtitle.metadata["text"]["content"]="Windows 0.4\n12 adjustments · RAW16 · PSD"
    subtitle.image=text_image(subtitle.metadata["text"])
    subtitle.transform.width,subtitle.transform.height=subtitle.image.size
    group=next(layer for layer in d.layers if layer.is_group)
    data=default_adjustment("Grain");data["grainSettings"]["amount"]=4
    d.layers.append(Layer("组内细颗粒",None,Transform(0,0,d.width,d.height),parent_id=group.id,metadata={"adjustment":data}))
    save_project(d,ROOT/"examples/创作工作台0.4.comp")
    export_image(d,ROOT/"examples/创作工作台0.4.png")
    (ROOT/"docs").mkdir(exist_ok=True)
    export_image(d,ROOT/"docs/preview.png")


if __name__=="__main__":create()
