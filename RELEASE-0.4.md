# Compositor Windows 0.4.0

独立 Windows 移植预览版，MIT 开源，参考 Compositor v1.4.6；非上游官方发行版。

下载 `Compositor-Windows-0.4-portable.zip`，解压完整文件夹后运行 `Compositor-Windows.exe`。Windows x64，无需 Python，离线 AI 模型随包提供；保留 `_internal`、`licenses` 和模型许可证。中文操作说明和可编辑示例已包含。

本轮补齐 12 种原版调整、四通道色阶和完整曲线编辑、八种原生 PSD 调整、PSD 兼容导出，以及 RAW 直接输出真实 16 位 TIFF。修复中文 PSD 名称、半透明像素往返、文字字偶距对齐、组移动与层序、剪贴循环检查、复制蒙版预算、GPU 和 DirectML 运行时回退。

已有 GPU 混合、原像素分块、后台文件/滤镜、离线主体分割、六种效果、可编辑 PSD 文字与组内调整。

验证：104 项回归通过；源码、便携 EXE 的 GPU/DirectML 与 CPU 路径通过。记录随包提供，ZIP 完整性与 SHA-256 已检查。发布资产 `SHA256SUMS-0.4.txt` 提供文件校验。

工作空间仍为 8 位 RGBA，RAW16 入口直接读取原始文件；GPU 加速部分合成，部分计算仍使用 CPU。PSD 不是完整 Photoshop 无损往返工具，兼容导出会明确合并受影响图层，`.comp` 保持可编辑。尚未在 Adobe Photoshop 或 Mac 原版中实测。完整边界见包内使用说明。
