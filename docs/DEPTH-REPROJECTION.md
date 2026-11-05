# 原生深度图重投影核验

2026-11-05，基点 [eeddc1b](https://github.com/sakorman/web-scan/commit/eeddc1b)。上一轮的候选按固定像素网格分组；本轮把每个分组代表点投影到其他训练照片的完整原生深度图，在真实投影坐标取双线性深度。目标像素不必落在候选网格上。该流程只做诊断，不修改 SfM、Brush、初始化点云或渲染。

## 依据与方法

[DepthSplat (CVPR 2025)](https://openaccess.thecvf.com/content/CVPR2025/html/Xu_DepthSplat_Connecting_Gaussian_Splatting_and_Depth_CVPR_2025_paper.html) 和其[公开实现](https://github.com/cvg/depthsplat)把深度估计与高斯重建的跨任务一致性作为共同约束；[ConfidentSplat](https://arxiv.org/abs/2509.16863)进一步按多视图几何可靠性给深度融合加权。本地验证采用其中可检验的部分：先用已有 RGB/SfM 深度先验生成代表点，再在完整原生图上检查跨视图相对深度。没有移植论文代码，也没有使用 CUDA 或传感器值参与 RGB 候选排序。

对每个半径与顺序的固定分组，读取代表的世界坐标和来源照片；在所有其他已通过单视图筛查的照片中做相机投影。投影在照片边界内、目标深度为正，且目标轴向深度与投影深度的相对误差不超过 5%，记作一个来源支持。TUM 另外在同一投影位置用独立 RGB-D 深度图按最近原生像素核验，使用既有全局尺度；传感器只作事后审计。相同照片不计第二个来源，留出照片仍不参与训练。

## 固定实拍结果

下表只比较上轮保存的 1% 正序分组。`native>=2` 是至少两个其他照片的原生投影支持，`sensor>=2` 是 TUM 中至少两个独立 RGB-D 测量也通过 5% 的组。

| 场景 | 输入 / 定位；训练 / 留出 | 1% 组 | 先前三视差组 | native>=2 | native>=3 | sensor>=2 |
|---|---:|---:|---:|---:|---:|---:|
| office | 26 / 26；22 / 4 | 4,047 | 224 | 4,043 | 2,058 | — |
| pipes | 14 / 14；12 / 2 | 4,580 | 882 | 4,580 | 3,517 | — |
| TUM desk | 96 / 96；84 / 12 | 1,184 | 29 | 1,184 | 1,113 | 1,054 |

TUM 的原生投影共检查 7,572 个目标视图位置，其中 6,408 个有有效独立传感器深度；传感器支持的 1% 分组中，1,054 组有至少两个通过的传感器来源。office、pipes 没有独立深度真值，`native` 结果只能说明模型深度先验的跨图一致性，不能证明几何准确。TUM 传感器也有时间、遮挡、缺测和 RGB/SfM 全局尺度误差，不能把支持组直接写入初始化。

分组顺序敏感性仍存在：本轮使用的 1% 反向结果为 office 4,117 组、pipes 4,449 组、TUM 1,147 组；对应原生 `native>=2` 分别为 4,117、4,449、1,147。原生重投影减少了“目标恰好落在网格上”的漏检，但不能消除固定代表和单目深度的偏差。

## 成本、验证与下一步

重投影审计在本机 M5 / 32 GB 上复用冻结预测和模型，office / pipes / TUM 分别耗 CPU 墙钟约 7.01 / 3.56 / 2.47 秒；未运行 GPU 训练、未改变模型文件，未测峰值内存。新增 `scripts/benchmark_depth_reprojection.py`，样本、分组、相机、预测和传感器文件均校验 SHA-256；新增两项输入形状、正深度和容差回归。下一步只在小规模且有明确独立真值的候选上比较初始化前后质量；若没有独立验证，将保留此审计而不融合点云。

复现（需要已有冻结基准）：

```sh
mkdir -p .data/benchmarks/depth-reprojection-repeat
PYTHONPATH=. .venv/bin/python scripts/benchmark_depth_reprojection.py \
  --samples .data/benchmarks/depth-groups-20261104/verified/tum-samples \
  --groups .data/benchmarks/depth-groups-20261104/verified/tum \
  --anchors .data/benchmarks/depth-overlap-20261103/sensor/anchors.json \
  --analysis .data/benchmarks/depth-overlap-20261103/analysis.json \
  --predictions .data/benchmarks/depth-overlap-20261103/predictions \
  --job tum-baseline --sensor-manifest .data/benchmarks/depth-overlap-20261103/sensor/sensor.json \
  --sensor-scale 0.2318619668017296 \
  --output .data/benchmarks/depth-reprojection-repeat/tum.json
```

