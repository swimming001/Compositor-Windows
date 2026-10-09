"""Write the Chinese release guide and task acceptance record."""
from pathlib import Path

ROOT=Path(__file__).resolve().parent
README='''# Compositor Windows 0.3 使用说明

这是参考 [Compositor v1.4.6](https://github.com/robbietilton/Compositor/tree/v1.4.6) 源码和格式开发的独立 Windows 移植预览版，非上游官方发行版。原始 Mac 源码、0.1 和 0.2 发行包均保留。

## 启动

保存并退出旧版后，双击项目根目录的 `启动Windows版.cmd`，或 `dist/v0.3/Compositor-Windows/Compositor-Windows.exe`。便携包为 `dist/Compositor-Windows-0.3-portable.zip`。解压整个文件夹再运行，不需要安装 Python；请保留 `_internal`、模型和许可证文件。

本构建为 Windows x64。已在当前 Windows 主机检查 AMD Radeon 610M 的 OpenGL 合成和 DirectML 主体分割，以及便携 EXE 的 CPU 合成/AI 路径；其他电脑的驱动情况可能不同，GPU 初始化失败时自动使用 CPU。菜单“帮助 → 关于”显示实际渲染设备。需要手动关闭 GPU 合成时，在启动前设置环境变量 `COMPOSITOR_GPU=off`；AI 推理需要 CPU 时设置 `COMPOSITOR_AI_DEVICE=cpu`。

## 操作

1. 打开 `examples/创作工作台0.3.comp` **文件夹**，体验文字、效果和组内调整。也可新建画布后导入照片。
2. 选中图层，按 `V` 移动，拖角点缩放、顶部圆点旋转，Shift 保持比例或约束角度。滚轮缩放、右键平移，`Ctrl+0` 适合窗口、`Ctrl+1` 实际像素。
3. 按 `B` 绘制、`E` 擦除。勾选“编辑蒙版”时，白色显示、黑色隐藏。裁剪工具 `C` 框选后 Enter 应用，Esc 取消。
4. `Ctrl+S` 保存 `.comp` 工程，`Ctrl+Shift+E` 导出 PNG/JPEG；“文件 → 导出分层 PSD”生成可编辑分层文件。导出不代替工程保存。

## GPU 与原像素分块

16 种混合模式使用 OpenGL GPU 合成；变换采样、部分效果、调整和 PNG/JPEG 编码仍由 CPU 完成，这是混合渲染管线。GPU 模式不是对每种工程都保证更快。预览可有一个字节以内的舍入差异，导出统一使用 CPU 原图计算。

拖动时保留低延迟预览。缩放达到 100% 或更高、操作停止后，后台按 384×384 文档像素计算可见块，状态栏显示“原像素分块”。高倍率显示不再只放大缩略图；原像素使用最近邻放大便于检查像素。快速平移或缩放时会暂时显示旧预览，再补上新块。模糊、阴影和发光使用额外采样边缘避免接缝。

完整导出读取源图，分块计算全部画布后编码；没有拿预览图片充当导出图。仍需分配最终输出图像，因此大画布会占用较多内存。

## AI 主体分割

选中像素图层，使用“图像 → AI 主体分割 → 蒙版”。默认完整 U²-Net 模型适合质量优先；“AI 主体分割（快速）”使用 U²-NetP，速度优先但可能漏掉细节。模型已随包提供并通过官方校验，不需要在线下载，也不会上传照片。

Windows 上优先使用 ONNX Runtime DirectML，初始化失败时改用 CPU。完成后生成灰度蒙版，保留原图，可撤销、禁用、反相并用画笔修整；已有蒙版与主体蒙版相乘。主体识别不是完美边缘抠图，头发、透明物体、细小边缘仍可能需要人工调整。它不等同于 Apple Vision 的实现。

## RAW 显影

通过“导入”选择 DNG、CR2/CR3、NEF、ARW、RAF、ORF、RW2、PEF、KDC 等文件。弹窗可设置曝光 -8 至 +8 EV、相机/自动/日光白平衡，或半尺寸解码。LibRaw 在后台读取相机原始数据并去马赛克，转换为 8 位 sRGB 可编辑图层；不是读取嵌入 JPEG 假装解码 RAW。

原 RAW 文件不会修改。当前是 RAW 导入显影，不是 Adobe Camera Raw 完整面板，也不是保持 16 位 RAW 参数随工程反复无损显影。相机与编码支持由 LibRaw 0.22.1 决定；超过尺寸预算时可尝试半尺寸选项。

## PSD 可编辑分层

导入 PSD/PSB 时读取真实图层，保留像素、名称、位置、可见性、透明度、16 种混合模式、组、图层蒙版和剪贴链接（包括组作为基础、被剪贴组）。文字读取为可编辑文字元数据；形状和智能对象转为独立像素图层，矢量蒙版转为可修整的像素蒙版。没有像素通道的常见填充/形状会单独栅格化填充。

PSD 导入到空工程时使用其画布大小；已有工程中导入时放入单独文件组并保留原坐标。双击文字重排内容；移动独立图层、绘制、修改蒙版后，可保存为 `.comp`，重开仍可继续编辑。

“文件 → 导出分层 PSD”写入原生独立像素图层、组、蒙版、剪贴、16 种混合、可编辑文字描述、六种图层效果，以及曝光、色阶、控制点曲线和反相调整层。带调整或效果的组写为隔离组。导出在后台执行，并写入本程序计算的完整合成预览；先校验临时 PSD，再替换目标文件。

PSD 只支持剪贴到同组的紧邻下方基础层；任意跨组/跳层剪贴、高斯模糊调整层、每通道超过 19 个曲线控制点会明确报错，目标 PSD 保持原样。请保留 `.comp` 工程，或调整层序/参数后再导出。图层透明度量化为 PSD 的 0–255；像素变换写入独立像素，原图与变换参数保留在 `.comp`。文字有原生变换，但第三方重排后的复杂样式、字体差异不能保证相同外观。

本版不是所有 Photoshop 功能的无损往返工具。未实现的混合/调整会报错；斜面浮雕、渐变叠加、图案叠加、光泽和部分填充/效果差异显示转换说明。局部字号、变形文字、段落细节和字体替换可能改变排版。已用 psd-tools 的原生结构解析及本程序验证图层、文字样式、蒙版、效果和调整层；当前电脑没有 Photoshop，尚未在 Adobe 软件中实测打开和编辑。导入操作不修改原 PSD。

## 六种图层效果

选中像素或文字图层，使用“图层 → 编辑图层效果”：描边、投影、颜色叠加、内阴影、外发光、内发光。可叠加，支持启用、颜色、不透明度及对应大小/距离/角度；描边可选内侧。效果保存为参数，源图不变，可实时预览、取消、撤销、重新编辑和移除。

## 组内调整层

“图层 → 新建图层组”创建文件夹，“移入图层组”修改归属。选中组或组内图层后，从“调整层”菜单添加曝光、色阶、曲线、反相、高斯模糊；调整影响本组内部下方的合成，不修改组外背景。含调整的组使用独立合成，再应用组蒙版、不透明度和混合；普通无调整组保留通透行为。

双击调整层重新修改参数，支持蒙版、透明度、启用/禁用和撤销。当前曲线面板控制 RGB 中点，色阶面板控制 RGB 输入三项；已导入的四通道参数保留。其他原版调整种类仍未移植。

## 文字排版

`T` 新建或编辑文字，双击文字层也可打开编辑器。支持原项目文本模型中的多行内容、系统字体、1–2000 字号、颜色、Left/Center/Right 对齐、字距、行距、固定宽高文本框与自动换行。行距 0 使用 120% 自动行距，文本框宽高同时为 0 使用自动大小。

选择一段文字后，可点“选区字体”或“选区颜色”设置局部样式；编辑内容会保留未改变字符的样式，新字符继承附近样式。范围使用 UTF-16 编码，兼容原工程里的 emoji 偏移。文本框溢出的内容被裁剪，但文字内容保留。字体缺失时以 Windows 微软雅黑替代；Mac/Windows 字形、字偶距和复杂语言塑形不能保证像素一致。

本版覆盖 Compositor 保存的文字排版字段，不是 Photoshop 的全部文字系统：局部字号、竖排、扭曲、完整两端对齐等仍可能需要保留像素外观或转换。直接绘制文字像素或使用像素滤镜后转为栅格图层。

## 后台操作与文件安全

导入、打开、保存、导出、像素滤镜、颜色抠图和 AI 分割都通过独立工作线程执行。操作期间可以缩放和平移。会替换工程内容的操作暂时暂停像素/图层编辑，成功后一次性应用，失败保持原工程；计算可取消并丢弃结果，当前计算本身可能需要先结束。

保存/导出记录开始时的快照，期间可继续编辑；后续修改继续显示未保存标记。保存完成才允许接续新建、打开或退出，保存失败不会丢弃内容。文件写入使用临时文件/工程替换，最终替换阶段不提供强行取消。

## 格式、预算与范围

画布/单张源图最多 6400 万像素，边长最多 12000，全部图层图像与蒙版最多 1.92 亿源像素、512 个图层。历史约 192 MiB、最多 30 步；预览缓存约 96 MiB。预算不是大图零卡顿的保证，画笔源图复制、文字排版等仍可能耗时。

工程使用 `com.compositor.project` v11，支持旧版 `.comp`；新增可选 `windows.isolated` 字段记录 PSD 组隔离语义，原版可忽略此字段。组剪贴等扩展未在 Mac 原程序做双向兼容实测，跨平台请先使用副本。仍未实现 HEIC/SVG、全部调整种类、完整 Photoshop 特效、16 位工作空间、选择/修复/内容识别等高级工具。

源码运行：`python -m pip install -r requirements.txt`，然后 `python app.py`。重新打包前用 `python fetch_v03_assets.py` 下载并校验模型，`python create_demo_v03.py` 生成示例，`python build.py` 输出 0.3。回归命令：`python -m unittest -v test_engine test_app test_v02 test_v03`。测试样例不随用户便携包提供。

功能验收、实际设备及限制见 `0.3-任务验收说明.md`。依赖与模型许可证见 `licenses/` 和 `_internal/models/`。
'''

ACCEPTANCE='''# Windows 0.3 逐项任务验收

对应用户从 0.2 指出的事项，提供可运行的 0.3 实现。各项的具体使用方法见 `README-中文.md`。这是独立移植的功能范围，不表示完全复刻所有 Apple/Adobe 实现。

| 用户要求 | 0.3 交付 | 验证 |
| --- | --- | --- |
| 使用 GPU | OpenGL GPU 执行 16 种合成；AI 优先 DirectML | AMD Radeon 610M 实际绘制；与 CPU 的 16 种混合逐像素误差最多 1 字节 |
| AI 分割 | 完整 U²-Net 质量模式及 U²-NetP 快速模式，离线生成可编辑蒙版 | 官方校验，真实人物照片的背景、躯干与双臂覆盖检查，DirectML 实际推理 |
| RAW | LibRaw 解码/去马赛克，曝光、白平衡、半尺寸选项 | 官方 RAW KDC 样例显影为 768×512；曝光变化、半尺寸 384×256 检查 |
| 图层效果 | 原版六种：描边、投影、颜色叠加、内阴影、外发光、内发光 | 六种结果、开关、源像素不变、工程保存、编辑/取消/撤销 |
| PSD 可编辑分层 | 真实分层导入、.comp 保存、原生 PSD 分层导出（文字/效果/四种调整/蒙版/组剪贴） | 官方 PSD 样例；独立图层移动；原生描述解析；标准 UTF-16 样式读取；导出重开；不支持项保留原文件 |
| 组内调整层 | 新建/归属图层组；组内五种调整可重新编辑 | 组内反相不影响组外背景；组透明度只应用一次；撤销/保存重开 |
| 原项目文字排版 | 全部保存字段：对齐、字距、行距、文本框、局部字体/颜色、UTF-16 范围 | 换行、对齐、行距、emoji 偏移、编辑继承、富文本工程往返 |
| 原像素分块 | 384×384 文档像素可见块、高倍率最近邻查看、滤镜边缘扩展 | 保住缩略预览丢失的单像素条纹；旋转/蒙版/混合与整图一致；模糊+六种效果接缝最多 1 字节 |
| 导入/保存/导出/像素滤镜离开主线程 | 后台工作、进度、取消计算结果、快照保存、失败回滚 | Tk 定时事件仍执行；取消不改变工程；后续修改仍保持未保存；保存失败不继续丢弃 |

## 自动测试与实际程序

`verification/v0.3-tests.txt` 保存 79 项通过的回归测试结果。新增测试是实际功能与失败边界检查，使用真实 Tk、GPU、RAW 和 AI 库。

`verification/v0.3-source/verification-result.json` 与 `verification/v0.3-frozen/verification-result.json` 记录源码和打包程序的实际验证，包含 GPU 设备/绘制次数、AI 设备、RAW 尺寸、PSD 图层数、文字/效果/组调整、400% 原像素显示、后台文件写入、原生 PSD 导出、工程与 PNG 导出像素一致及 Tk 错误数。验证图片和窗口截图保存在源码目录对应文件夹，便携包附测试日志和 JSON 验证摘要。

本次检查针对当前 Windows x64 主机。CPU/GPU 的总体速度会受上传/回读、图片和驱动影响，未声称整条流程加速固定倍数。冷启动、AI 首次模型加载、巨大采样半径和最终输出图像仍有实际时间/内存成本。

`verification/v0.3-frozen-cpu/verification-result.json` 另外验证便携 EXE 在关闭 GPU 后使用 CPU 合成与 CPU AI 推理，RAW、分层 PSD、原像素块、后台保存/导出仍正常。GPU 与 CPU 两次程序验收均无 Tk 错误。

## 明确边界

- GPU 是合成与 AI 的加速实现；不是所有操作全部迁移 GPU。PNG/JPEG 导出统一走 CPU 以保持稳定结果。
- RAW 输出为 8 位 sRGB 图层；没有 Adobe Camera Raw 全套面板或 16 位无损参数工程。
- PSD 提供分层导入/导出及 .comp 保存。高斯模糊调整、任意跨组/跳层剪贴等无法原样写成 PSD；额外 Photoshop 效果显示转换说明。尚未在 Adobe Photoshop 中实测；原生描述已解析和回归验证。
- 文字覆盖原项目 `LayerTextStyle` 字段，不覆盖 Photoshop 的所有排版特性。原字体缺失、复杂塑形、局部字号、竖排和文字扭曲等可能出现差异。
- 含调整的组隔离合成；Windows 可选组隔离字段不会自动保证 Mac 原版相同外观。尚未做 Mac 双向实测。

## 参考和许可证

实现对照本地 MIT 上游源码：`LayerEffects.swift`、`TypeTool.swift`、`LayerAdjustment.swift` 和工程格式。GPU 使用 [ModernGL 官方文档](https://moderngl.readthedocs.io/en/latest/topics/context.html)，PSD 使用 [psd-tools API](https://psd-tools.readthedocs.io/en/latest/reference/psd_tools.api.layers.html)，RAW 使用 [rawpy 官方参数](https://letmaik.github.io/rawpy/api/rawpy.Params.html)，AI 预处理和模型校验对照 [rembg 官方会话](https://github.com/danielgatis/rembg/tree/main/rembg/sessions) 和 [U²-Net](https://github.com/xuebinqin/U-2-Net)。Windows DirectML 参考 [ONNX Runtime 文档](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)。

模型权重未改动，附上游 Apache-2.0 与 rembg MIT 许可证及校验文件。rawpy/LibRaw 二进制附原许可；对应 [rawpy 源码](https://github.com/letmaik/rawpy/tree/v0.27.1) 与 [LibRaw 0.22.1 源码](https://www.libraw.org/download) 可按原许可获取。其他可定位到的依赖许可证复制至发行 `licenses/`。
'''

(ROOT/"README-中文.md").write_text(README,encoding="utf-8")
(ROOT/"0.3-任务验收说明.md").write_text(ACCEPTANCE,encoding="utf-8")
