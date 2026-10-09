from pathlib import Path
from psdio import load_psd
from engine import render

for path in Path("verification/v0.3-fixtures").glob("*.psd"):
    try:
        d,notes=load_psd(path)
        im=render(d,(400,400))
        im.save(path.with_suffix(".imported.png"))
        print(path.name,len(d.layers),notes,flush=True)
    except Exception as error:
        print(path.name,type(error).__name__,str(error),flush=True)
