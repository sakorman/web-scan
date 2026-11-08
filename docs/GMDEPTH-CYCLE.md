# 双向深度一致性筛查

2026-11-08，基于提交 [0f21129](https://github.com/sakorman/web-scan/commit/0f21129) 的冻结 pipes、office 与 TUM 结果，验证“正向和反向 GMDepth 都能重投影通过”的区域是否更可靠。实验只生成离线掩码和统计，没有向初始化或 Brush 注入点，也没有改变网页默认流程。

## 研究依据

[GVGS](https://arxiv.org/abs/2601.20331v3)（Mai Su 等，2026，代码仓库 [sumai-1998/GVGS](https://github.com/sumai-1998/GVGS)）把可见性放到 Gaussian primitive 层，并用几何一致性筛选单目先验；[Confidence matters](https://arxiv.org/abs/2608.06117)（Hongyu Zhou、Zorah Lähner，2026）报告多视图先验和置信图比单视图先验更适合几何监督。这里先验证其中“置信区域应有更低误差”的可测部分，未复制上游训练代码。UniMatch GMDepth 源码仍固定为 [943a990](https://github.com/autonomousvision/unimatch/tree/943a99006950ab6c84ea72a72beb147fdd03c19a)，权重 SHA-256 和许可边界沿用 [GMDEPTH.md](GMDEPTH.md)。

## 规则与输入

- 正向图对沿用上一轮的 13 个冻结实拍配对；反向图对交换 source/target，并重新用 source 的 SfM Z 范围推理。每个方向独立运行 MPS，浮点、网络尺寸、64 个逆深度候选、相机缩放和半像素坐标与原实验一致。
- 在原始 RGB 检查格上取正向深度，按相对位姿反投影到反向图，再检查深度一致性、两像素往返误差和至少 1° 视差。相对深度差阈值为 5%。分类为 `supported`、`occlusion_candidate`、`front_depth_conflict`、`roundtrip_failed` 或 `low_parallax`，所有位置恰好归入一类。
- ETH3D 只报告一致性覆盖；TUM 使用此前固定的原始 RADIAL 传感器网格作事后评分。传感器检查格不参与掩码选择，不能把 TUM 结果称为纯照片米制精度。

## 结果

| 场景 | 检查位置 | 双向支持 | 支持率 | 遮挡候选 | 前方冲突 |
| --- | ---: | ---: | ---: | ---: | ---: |
| pipes | 53,067 | 30,424 | 57.33% | 3,526 | 1,334 |
| office | 53,067 | 5,814 | 10.96% | 18,288 | 743 |
| TUM | 104,390 | 63,613 | 60.94% | 5,232 | 3,771 |

TUM 传感器检查格的逐视图平均 AbsRel 如下：

| 区域 | GMDepth | 单目基线 |
| --- | ---: | ---: |
| 全部 | 7.35% | 5.66% |
| 双向支持 | 4.28% | 4.49% |
| 被拒区域 | 13.28% | 9.46% |

支持区域的 GMDepth 比单目基线低约 0.21 个百分点，但只覆盖 60.94% 的检查位置；被拒区域两种预测都明显更差，说明掩码可作为风险提示，不能恢复成完整深度真值。全区双视图仍劣于单目，故不接入补点或训练损失。统计图见 [200-gmdepth-cycle.png](screenshots/200-gmdepth-cycle.png)，逐场景摘要与哈希见 [gmdepth-cycle.json](benchmarks/gmdepth-cycle.json)。

第一次评分时用 JSON 字典严格比较冻结浮点统计，因 NumPy 归约顺序产生约 `1e-16` 的差异而中止；改为 `rtol=1e-12、atol=1e-12` 后重新评分，所有冻结指标逐项一致。该失败记录在本轮日志中，未改变预测或掩码。

## 成本、边界与下一步

反向 MPS 推理共 13 个图对；本轮未将其与上一轮不同输入尺寸的前向时间拼成加速比，也未测峰值内存。工具会校验 SfM、模型、照片、上游提交和权重摘要；任何输入变化都会拒绝评分。当前结果仅证明双向一致性可以筛出一个较低误差子集，不能证明遮挡分类可靠或能提升 PSNR、SSIM、漫游空洞和细小结构。下一步若继续，应在独立深度真值或有可见性标注的数据上验证分类，再考虑候选初始化对照。

复现需要先准备上轮 `.data/benchmarks/gmdepth-20261107`，然后运行：

```sh
PYTHONPATH=. .venv/bin/python docs/benchmarks/gmdepth-cycle-run.py \
  --root .data/benchmarks/gmdepth-cycle-repeat
PYTHONPATH=.:.data/research/plotting .venv/bin/python \
  docs/benchmarks/gmdepth-cycle-report.py \
  --root .data/benchmarks/gmdepth-cycle-repeat
```

运行目录必须为空；预测、权重和照片均留在 `.data`，不会提交到仓库。
