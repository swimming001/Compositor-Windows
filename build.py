"""Build a portable Windows folder; do not modify system installations."""
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
from version import VERSION


def main():
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
            "--name", "Compositor-Windows", "--icon", str(ROOT/"app.ico"),
            "--distpath", str(ROOT/"dist"/f"v{VERSION}"), "--workpath", str(ROOT/"build"/f"v{VERSION}"),
            "--specpath", str(ROOT/"build"/f"v{VERSION}"), "--copy-metadata", "psd-tools"]
    args += ["--collect-binaries","onnxruntime","--collect-data","onnxruntime", "--collect-all","rawpy",
             "--collect-submodules","glcontext", "--hidden-import","scipy.ndimage", "--hidden-import","moderngl"]
    for module in ("skimage", "matplotlib", "torch", "tensorflow", "pandas", "cv2",
                   "IPython", "pytest", "PySide6", "PyQt5", "sympy", "onnxruntime.transformers", "onnxruntime.quantization", "onnxruntime.tools"):
        args += ["--exclude-module", module]
    args.append(str(ROOT/"app.py"))
    subprocess.run(args, cwd=ROOT, check=True)
    release = ROOT/"dist"/f"v{VERSION}"/"Compositor-Windows"
    for filename in ("README.md", "README-中文.md", "NOTICE.md", "LICENSE", "0.4-更新与验收.md", "0.4.1-保存修复说明.md"):
        shutil.copy2(ROOT/filename, release/filename)
    shutil.copytree(ROOT/"examples", release/"examples", dirs_exist_ok=True)
    shutil.copytree(ROOT/"models",release/"_internal"/"models",dirs_exist_ok=True)
    verification=release/"verification"
    verification.mkdir(exist_ok=True)
    shutil.copy2(ROOT/f"verification/v{VERSION}-tests.txt",verification/f"v{VERSION}-tests.txt")
    for mode in ("source","frozen","frozen-cpu"):
        source=ROOT/"verification"/f"v{VERSION}-{mode}"/"verification-result.json"
        if source.is_file():
            destination=verification/f"v{VERSION}-{mode}"
            destination.mkdir(exist_ok=True)
            shutil.copy2(source,destination/source.name)
    licenses = release/"licenses"
    licenses.mkdir(exist_ok=True)
    for name in ("Pillow", "numpy", "psd-tools", "aggdraw", "attrs", "typing_extensions", "packaging", "PyInstaller", "altgraph", "pywin32-ctypes",
                 "moderngl","glcontext","rawpy","onnxruntime-directml","scipy","protobuf","flatbuffers"):
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        for file in distribution.files or []:
            if any(word in str(file).lower() for word in ("license", "copying", "notice")):
                path = Path(distribution.locate_file(file))
                if path.is_file():
                    destination = licenses/name/str(file).replace("../", "").replace("..\\", "")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
    python_license = Path(sys.base_prefix)/"LICENSE.txt"
    if python_license.is_file(): shutil.copy2(python_license, licenses/"Python-LICENSE.txt")
    for name in ("tcl8.6", "tk8.6"):
        source = Path(sys.base_prefix)/"tcl"/name/"license.terms"
        if source.is_file(): shutil.copy2(source, licenses/f"{name}-license.terms")
    archive = shutil.make_archive(str(ROOT/"dist"/f"Compositor-Windows-{VERSION}-portable"), "zip", release.parent, release.name)
    print(f"Portable package: {archive}")


if __name__ == "__main__":
    main()
