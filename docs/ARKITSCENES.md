# ARKitScenes 的设备轨迹、深度与完整 SH3 模型

使用 Apple ARKitScenes 官方训练样本 47333462。从覆盖 59.49 秒的轨迹等间隔取 96 个时间戳，每个时间戳取最近的同步彩照、深度及逐帧内参，最大时间差 0.50 毫秒。从同一批 256×192 照片出发，纯照片 COLMAP 的最大模型仅包含 20 个视角；使用采集附带的相机轨迹与深度可以训练全部 96 个视角对应的场景。设备采集和上游轨迹生成时间没有计入本机处理时间。[Gilad Baruch 等，ARKitScenes](https://arxiv.org/abs/2111.08897) · [官方原始数据格式](https://github.com/apple/ARKitScenes/blob/main/DATA.md) · [数据许可](https://github.com/apple/ARKitScenes/blob/main/LICENSE)

## 训练与评分

相机使用每帧的 PINHOLE 内参，沿用官方轨迹的世界到相机方向。深度单位从毫米换算为米，只用 84 张训练照片的有效深度生成 16,118 个带颜色初始点。12 张留出照片的颜色和深度均未用于高斯训练和点初始化，采集附带的轨迹可能已经使用过这些视角。每个原始 JPEG 都保持原有编码。Brush 0.3.0 Metal 使用种子 42、1000 步、15 万高斯上限，训练分辨率上限 512，留出渲染实际为 256×192。[相机坐标约定参考实现](https://github.com/apple/ARKitScenes/blob/main/threedod/benchmark_scripts/utils/tenFpsDataLoader.py) · [逐帧评分与失败视角](benchmarks/arkit-47333462.json)

| 观测及方法 | 覆盖 | 本机计时 | 结果 |
| --- | ---: | ---: | --- |
| 纯照片，全配对、共享 RADIAL | 20/96 | 稀疏阶段 1.66 秒 | 低于任务的最低覆盖率，未训练 |
| 纯照片，全配对、自动 SIMPLE_RADIAL | 20/96 | 稀疏阶段 1.90 秒 | 最大注册模型未达到最低覆盖率 |
| 已有轨迹、内参和配准深度 | 96/96 | 本地归档到模型及下载校验 4.83 秒 | 30,716 个高斯，完整 SH3 PLY 7,250,526 字节 |

全部数据已在本机。新的独立任务从本地归档生成输入用 0.33 秒，导入用 0.28 秒，工作进程读取冻结相机和点用 0.086 秒，训练与导出用 3.72 秒，端到端包含排队、进程启动、状态轮询和产物检查。两种 COLMAP 配置没有完整模型可作画质或端到端加速比对照。数据集没有在本实验中提供独立的轨迹精度真值。实际留出渲染和相同尺寸原照片逐像素评分为 **18.533 dB / 0.70923 SSIM**。海报边缘和部分文字可辨，窗边植物、暖气片和柜门细节明显模糊。截图在本地实验目录 `heldout.jpg`，显示第 0、48、88 帧。此次检查固定拍摄视角。

## 小尺寸 JPEG 的训练兼容性

同一份采集最初通过导入校验，但 Brush 读取照片时报告提前到达文件末尾。把位姿模型从标准文本转换成二进制后仍出现相同错误。96 张 JPEG 中有 16 张长度小于该版读取器的首个缓冲区；照片可正常解码。现在训练时仅对这些照片创建临时副本，在 JPEG 元数据区增加注释段，像素、尺寸和原有 Exif 均保留。16 张共增加 28,247 字节。原始文件和冻结副本继续按原摘要校验。两次实拍训练均完成；第一次重试报告的 Brush PSNR 为 18.64 dB，独立验收为 18.53 dB。首次重试复用已导入的场景，未用于完整流程计时。[Brush 0.3.0 图像读取实现](https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-dataset/src/scene.rs)

## 复现

将官方的 `lowres_wide.traj`、`lowres_wide.zip`、`lowres_wide_intrinsics.zip` 和 `lowres_depth.zip` 放入同一目录。以下命令创建一份新的训练输入，并将它导入工作台。源数据和模型均保存在本机。该采样脚本为每八帧留出一帧的深度初始化准备，未估计新的采集轨迹。

```sh
.venv/bin/python -m scripts.prepare_arkitscenes \
  --assets .samples/archives/arkitscenes-47333462 \
  --out .data/benchmarks/arkit-reproduction/posed
.venv/bin/python -m webscan.import_poses .data/benchmarks/arkit-reproduction/posed \
  --name "ARKitScenes 47333462"
```

本次只检验一段低分辨率室内采集。下一步固定相机与留出视角，单独检查缺少实测深度时的初始几何。[已知位姿导入命令与 ETH3D 基准](POSED-IMPORT.md)
