# 反向深度：单个视角的改善能否推广？

上轮在 TUM fr1/desk 的目标照片 0025 上发现：反向推理时，用目标图自身的深度搜索范围，比沿用源图的范围更准。本轮把同批冻结预测中其余 9 个不同目标也与传感器核对。独立范围在 5 个新目标上更准、4 个上更差；新增目标的平均相对误差几乎相同。保留逐视角范围作为离线核验的定义，不据此更改默认重建或向训练加入深度。

## 相同预测，扩展传感器参考

[IDESplat（Wei Long 等，CVPR 2026）](https://openaccess.thecvf.com/content/CVPR2026/html/Long_IDESplat_Iterative_Depth_Probability_Estimation_for_Generalizable_3D_Gaussian_Splatting_CVPR_2026_paper.html) 在推理中迭代细化候选深度（[作者实现，MIT，eb0c863](https://github.com/CVL-UESTC/IDESplat/tree/eb0c8630d3679f5cb9ee4b665c855d5543dfbf8d)）。这里仅检验一个静态选择：在其他条件相同时，反向匹配按目标相机的 SfM 观测估计近远界，能否降低新增目标的平均误差？[UniMatch（Haofei Xu 等，MIT，943a990）](https://github.com/autonomousvision/unimatch/blob/943a99006950ab6c84ea72a72beb147fdd03c19a/unimatch/matching.py#L214-L218) 的内置双向路径共用源图的深度候选；上轮已经验证独立反向在近远界对齐后复现其结果。[基线提交](https://github.com/sakorman/web-scan/commit/f8fc12f142fb9578c4dd04a74780c48b608bfbda) · [权重、版本和适用范围](GMDEPTH.md)

复用上轮 MPS 生成的全部 11 对 TUM 反向预测，先冻结目标名单，再从[原始 RGB-D 序列（Sturm 等，CC BY 4.0）](https://cvg.cit.tum.de/data/datasets/rgbd-dataset/file_formats)选取时间最近的已配准深度帧。11 对涉及 10 张不同的目标照片，全部满足预设 20 毫秒阈值，时差为 2.672–18.372 毫秒。0025 的参考文件与逐项得分复现上轮；0011 对应两个源图，先平均这两个图对的误差，再让每个目标在总体均值中占相同权重。

原始共享 RADIAL 相机为 640×480，去畸变后为 626×469，预测为 512×384。沿用同一全局尺度（0.23186197 米/SfM 单位）和原图上间隔 4 像素的检查格，排除尺度拟合格。每个目标有 9,600 个检查位置，其中 110 个超出预测画幅。按不同目标去重后，画幅内的 94,900 个位置有 72,032 个有效传感器测量，22,868 个缺测；有效位置的两种预测均有效。原始深度除以 5,000 换算为米，零值不参与评分。AbsRel 为有效像素上绝对深度误差与传感器深度之比的均值，越低越好。

| 目标集合 | 目标 / 图对 | 共享范围 AbsRel | 独立范围 AbsRel | 独立范围更低 |
| --- | ---: | ---: | ---: | ---: |
| 上轮已评分的 0025 | 1 / 1 | 19.533% | 17.568% | 1/1 |
| 本轮新增目标 | 9 / 10 | 4.917% | 4.926% | 5/9 |
| 全部目标 | 10 / 11 | 6.379% | 6.190% | 6/10 |

全部目标的均值受 0025 影响；单看新增目标，独立范围的误差反而高 0.0085 个百分点。两种选择各有例外：0038 从 7.022% 降到 6.750%，0082 从 3.897% 升到 4.471%。[逐图对误差、有效像素数与文件摘要](benchmarks/gmdepth-targets.json)保留了这些差异。此次 CPU 关联、文件核验和评分共 3.40 秒，未重新推理或测量冷启动。RGB-D 仅用于离线尺度与检查，不作训练输入；未使用官方相机位姿、雷达或 CUDA，也未复现 IDESplat 模型。这些目标仍来自同一室内序列的训练视角。下一步若检验能否跨场景推广，应先固定另一序列的图对、尺度拟合与评分划分。

## ETH3D 纯照片重建回归

两次独立训练在 Brush 0.3.0 / Metal 串行完成；浏览器漫游在训练结束后进行。两场景均复用共享 RADIAL 的 RGB SfM 相机（本轮新增 SfM 耗时为 0），定位无失败。训练种子 42，3,000 步，长边 512，高斯上限 15 万；留出照片参与 SfM，不参与高斯训练。按固定相机、照片和 512×341 原生评分画幅测量，读回完整 SH3 PLY。这里记录纯 RGB 控制结果，不用于估计深度策略的画质或速度收益。

| 场景 | 定位/输入 | 训练/留出 | 高斯数 / PLY 字节 | 训练评估导出 / 含准备总耗时 | 留出 PSNR / SSIM |
| --- | ---: | ---: | ---: | ---: | ---: |
| pipes | 14/14 | 12/2 | 11,506 / 2,716,966 | 23.130 / 23.186 秒 | 19.0134 dB / 0.77879 |
| office | 26/26 | 22/4 | 14,227 / 3,359,122 | 23.625 / 23.701 秒 | 21.4191 dB / 0.88199 |

[pipes 同相机照片与渲染](screenshots/204-targets-pipes.jpg)中细管边缘偏软；从照片 9 转入漫游后，管道出现重影。[office 同相机照片与渲染](screenshots/204-targets-office.jpg)中细线不清；从照片 17 拖动后，桌前缘出现拉长的暗区。两个漫游视角不足以判断全场景空洞。ETH3D 图像与对照图依据 Schöps 等人的 CC BY-NC-SA 4.0 许可使用，见[第三方说明](../THIRD_PARTY_NOTICES.md)。TUM 复用[既有 96/96 定位、84/12 训练/留出重建](DEPTH-OVERLAP.md)，没有新的 TUM RGB 留出成绩。

## 复现

先按[双向深度基线](GMDEPTH-BIDIR.md)准备冻结预测、原始 TUM 归档和相机。每次运行选择不存在的输出目录；程序校验旧预测、相机、传感器与基线评分的身份。

```sh
base=.data/benchmarks/gmdepth-20261107
prev=.data/benchmarks/gmdepth-bidir-20261109
cycle=.data/benchmarks/gmdepth-cycle-20261108/run
out=.data/benchmarks/gmdepth-targets-repeat
.venv/bin/python -m scripts.benchmark_gmdepth_targets \
  --pairs "$base/tum/pairs.json" --predictions "$prev/tum" --reverse "$cycle/tum-reverse" \
  --old-score "$prev/tum-score/sensor.json" --sensor-root .data/benchmarks/depth-overlap-20261103 \
  --archive .samples/archives/tum-freiburg1-desk.tgz --output "$out"
PYTHONPATH=. .venv/bin/python docs/benchmarks/gmdepth-controls.py --root "$out"
```
