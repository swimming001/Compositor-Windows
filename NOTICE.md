# Attribution and port status

Upstream: Compositor v1.4.6, https://github.com/robbietilton/Compositor/tree/v1.4.6

Copyright (c) 2026 Wonder Assembly LLC. Upstream is licensed under MIT; its license is retained in LICENSE.

This independent Windows implementation follows the upstream document schema, layer ordering, transform conventions, masks and familiar editing workflow. It replaces Apple-only APIs with Tk, Pillow and NumPy. It does not bundle or execute the Mac application, and does not claim upstream endorsement or full feature parity.

New Windows implementation code is distributed under the same MIT license, with the upstream copyright and permission notice retained.

Third-party runtime components include Python and Tcl/Tk, Pillow, NumPy and its bundled math libraries, psd-tools, aggdraw, attrs, typing_extensions and packaging. PyInstaller and its hooks are used to build the executable; the PyInstaller bootloader has a distribution exception for bundled applications. Preserve bundled license files when redistributing.

Versions 0.3 and 0.4 also use ModernGL/glcontext, rawpy/LibRaw 0.22.1, SciPy and ONNX Runtime DirectML. LibRaw's dynamic binary and its license are bundled via rawpy; its corresponding source is available at https://www.libraw.org/download and the bindings/build scripts at https://github.com/letmaik/rawpy/tree/v0.27.1.

Unmodified U2-Net and U2-NetP model weights are redistributed from rembg's public v0.0.0 release, with U-2-Net's Apache-2.0 license, rembg's MIT license and model checksums beside the weights. Model architecture/source: https://github.com/xuebinqin/U-2-Net. Preprocessing/checksum reference: https://github.com/danielgatis/rembg/tree/main/rembg/sessions. Images stay on the local machine.

Primary implementation references:

- https://github.com/robbietilton/Compositor/blob/v1.4.6/docs/project-format.md
- https://github.com/robbietilton/Compositor/blob/v1.4.6/docs/writing-comp-files.md
- https://github.com/robbietilton/Compositor/blob/v1.4.6/Compositor/Document/LayerTransform.swift
- https://github.com/robbietilton/Compositor/blob/v1.4.6/Compositor/Document/LayerAppearance.swift
- https://pillow.readthedocs.io/en/stable/reference/Image.html
- https://psd-tools.readthedocs.io/en/stable/usage.html
- https://pyinstaller.org/en/stable/usage.html
