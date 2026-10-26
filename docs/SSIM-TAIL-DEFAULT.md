# 张量尾部修复：默认质量档复核

后续：[6000步模型感知复查](PERCEPTUAL-LONG.md)补充LPIPS评分，office均值同样变差，原接入决定保持。2026-10-26将下表pipes PSNR更正为已记录的统一512尺寸成绩，原生指标仍保存在JSON中。

后续：[SSIM 权重短对照](SSIM-WEIGHT.md)在pipes提高结构相似度但降低PSNR，默认权重仍为0.2。

2026-10-25。上一轮定位并修复了 CubeCL 连续复制在部分宽度上的尾部漏写。本轮用同一修复前 / 修复后二进制完成 6000 步、1024 目标长边的三场景12次对照，确认修复后输出有效，但没有达到默认接入条件：office 平均PSNR下降0.264 dB、SSIM下降0.00760，超过预先设定的0.2 dB / 0.005门槛。正式Brush和应用默认路径保持不变。

## 实验设置

Apple M5 / 32 GiB，Metal；两组使用相同Brush实验源、相同适配器和相同固定几何，只切换包含CubeCL尾部修复的二进制。缓存、profile、状态读回和其它SSIM实验开关全部关闭。6000步、1024目标长边、15万高斯预算、SH3；每100步增点，第4800步停止增长，每500步导出，第6000步留出评估。种子42，GPU串行。

| 场景 | 输入 / 定位 | 训练 / 留出 | 实际训练与评估尺寸 |
| --- | ---: | ---: | --- |
| ETH3D pipes | 14 / 14 | 12 / 2 | 1024×673 |
| ETH3D office | 26 / 26 | 22 / 4 | 1024×667 |
| TUM desk | 96 / 96 | 84 / 12 | 626×469 |

每8张照片留出一张，留出照片仍参与SfM。应用默认使用全部已定位照片；本对照保留留出拆分，只用于固定几何下的同视角渲染筛查。没有新SfM、API端到端或峰值内存计时，系统和驱动缓存未清空。

## 结果

使用 `compare_quality.py` 将所有评估图和参考图统一到长边512，计算RGB PSNR和11×11 Gaussian-window SSIM，视角等权平均。筛查门槛在运行前写入计划，失败结果完整保留。

| 场景 | 修复前平均耗时 | 修复后平均耗时 | 耗时变化 | PSNR：前 → 后 | SSIM：前 → 后 | 筛查 |
| --- | ---: | ---: | ---: | --- | --- | --- |
| pipes | 195.811 s | 197.334 s | +0.78% | 16.7509 → 17.3464 | 0.74597 → 0.74990 | 通过 |
| office | 191.420 s | 193.938 s | +1.32% | 20.8826 → 20.6190 | 0.87347 → 0.86587 | 未通过 |
| TUM desk | 136.533 s | 137.028 s | +0.36% | 23.2843 → 23.5049 | 0.84552 → 0.84645 | 通过 |

耗时没有形成收益，修复后的三组平均都略慢。pipes和TUM质量通过工程筛查，office没有通过，因此不能把这个修复作为默认质量改进。每组只有两次重复，不构成统计非劣效证明。

两组共保留12个完整模型。实际高斯数、PLY大小、消费顺序、相机摘要、评估图摘要和逐视角成绩见[公开JSON](benchmarks/ssim-tail-default.json)。修复前后模型的差异仍受训练路径中其它GPU调度和点增殖波动影响，不能把单个视角的改善归因于尾部修复。

## 输出和页面检查

所有模型通过完整SH3和留出文件校验。浏览器加载三场景修复前 / 修复后模型，拖动三个候选进入室内漫游，控制台无错误或警告。薄管、线缆、小字和桌面细节仍偏软，采集覆盖之外继续有空洞、拉丝和漂浮片段。

| 场景 | 修复前 | 修复后 |
| --- | --- | --- |
| pipes | ![pipes修复前](screenshots/122-ssim-tail-default-pipes-baseline.png) | ![pipes修复后](screenshots/123-ssim-tail-default-pipes-candidate.png) |
| office | ![office修复前](screenshots/124-ssim-tail-default-office-baseline.png) | ![office修复后](screenshots/125-ssim-tail-default-office-candidate.png) |
| TUM desk | ![TUM修复前](screenshots/126-ssim-tail-default-tum-desk-baseline.png) | ![TUM修复后](screenshots/127-ssim-tail-default-tum-desk-candidate.png) |

图片是首次重复第一个留出视角的原生评估PNG，未修图或换成原照片；ETH3D图片保留CC BY-NC-SA 4.0，TUM图片保留CC BY 4.0。

## 依赖和复现

实际构建使用Brush `3edecbb2fe79d3e2c87eeab85b15e0b1dd10d486`、CubeCL `43580a8c294f3a8140921c26f6378a12aef82881`。公开补丁分为三部分：

- `benchmarks/ssim-tail.patch`：Brush累计实验源码补丁；
- `benchmarks/cubecl-contiguous-tail.patch`：CubeCL连续复制的两行修复；
- `benchmarks/ssim-tail-brush-deps.patch` 和 `benchmarks/ssim-tail-cubecl-deps.patch`：将这两个本地补丁接入Cargo依赖的配置差异。

这四个补丁合在独立源码副本中得到本轮二进制；Cargo锁文件除 `cubecl-std` 的来源覆盖外保持一致。摘要和锁文件哈希见JSON。官方Brush安装包没有被覆盖。

参考[SSIM原论文](https://ece.uwaterloo.ca/~z70wang/publications/ssim.pdf)、[CubeCL连续复制实现](https://github.com/ArthurBrussee/cubecl/blob/43580a8c294f3a8140921c26f6378a12aef82881/crates/cubecl-std/src/tensor/contiguous.rs)和[Faster-GS论文](https://fhahlbohm.github.io/faster-gaussian-splatting/assets/hahlbohm2026fastergs.pdf)。本轮没有移植CUDA内核或论文中的其它优化，也没有把短期或单场景成绩写成通用性能承诺。
