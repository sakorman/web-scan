# PLY 连续块数值校验

2026-10-30。完整 SH3 模型读取后，按连续记录分块检查有限值，减少逐字段访问交错内存的重复扫描。点数、字段要求、球谐完整性、归一化与输出格式均不变。[实现提交](https://github.com/sakorman/web-scan/commit/c4ad755) · [逐次数据](benchmarks/ply-validation-scan.json)

## 问题与改动

上一轮已省掉导出时的整文件字节副本。本轮从该版本建立新基线：30 万高斯的 pipes 模型转换为 0.096845 秒，进程峰值 RSS 为 145,489,920 字节。另一次诊断剖面中，总转换约 0.088 秒，`read_ply` 约占 0.050 秒，其中文件读取约 0.006 秒；主要剩余成本是逐字段数值校验。诊断计时没有混入正式对照。

PLY 的字段由解析器固定为紧凑排列的小端 float32。原实现分别读取每个必需字段，SH3 模型会多次跨过同一块交错数据。新实现先检查字段是否齐全，再将同一数据缓冲区视作二维浮点记录，按连续块检查，每块布尔临时数组最多 1 MiB。必需字段按名称映射到实际列，未使用的附加字段仍沿用原有规则，避免因扫描顺序改变而扩大拒绝范围。

新增 10 项回归，覆盖 40,001 条记录的首部、中部、尾部 NaN / 正负无穷，倒序字段，以及未使用法线字段的原有处理规则；异常必需参数仍在任何下载文件生成前被拒绝。

## 固定真实模型对照

基线为 [8617074](https://github.com/sakorman/web-scan/commit/8617074)，已包含上一轮直接写数组优化。复用历史 ETH3D pipes / office 的同一原始 Brush 最终 PLY 和归一化参数，各做三次交错重复；每次启动独立 Python 进程，共 12 次。历史模型均为 10,000 步 / 1024 像素，pipes 为 14 / 14 定位、12 / 2 训练与留出，office 为 26 / 26、22 / 4；本轮转换时没有重训这些大模型。源文件、最终 PLY 和 splat 的 SHA-256 与大小全部一致。

| 模型 | 高斯数 | PLY / splat 字节 | 转换中位数：基线 → 候选 | 减少 | 峰值 RSS 中位数 |
| --- | ---: | --- | --- | ---: | --- |
| pipes | 300,000 | 70,801,551 / 9,600,000 | 97.312 → 51.922 ms | 46.64% | 138.109 → 138.172 MiB |
| office | 231,428 | 54,618,559 / 7,405,696 | 65.909 → 40.863 ms | 38.00% | 112.859 → 112.859 MiB |

pipes 的三次范围为基线 91.439–106.516 ms、候选 49.510–58.103 ms；office 为 64.352–66.267 ms、37.692–46.349 ms。两组输出逐字节相同，保留此改动。峰值内存基本不变，没有宣称进一步省内存。

这些时间包含读取、校验、归一化、转换和写出，不含 Python 导入、摘要计算、SfM 或训练。文件系统缓存未清空，没有额外 `fsync`；不是冷盘吞吐，也不能把 38%–47% 的转换变化套用到整条重建。独立转换不使用 GPU。

## 真实照片重建

随后串行执行 Brush 0.3.0 / Metal：同镜头 RADIAL、3,000 步、512 像素长边、30 万高斯预算、SH3、种子 42，每 8 张留出一张。两组都复用已校验的 SfM 缓存，源照片 1600×1067，留出图及评分为 512×341，没有浏览器查看器并行运行。

| 场景 | 输入 / 定位 | 训练 / 留出 | 高斯 / PLY 字节 | 缓存恢复 / 训练导出 / 总任务秒数 | PSNR / SSIM |
| --- | --- | --- | --- | --- | --- |
| pipes | 14 / 14 | 12 / 2 | 11,521 / 2,720,506 | 0.0225 / 23.2636 / 23.33 | 19.218977 / 0.78707170 |
| office | 26 / 26 | 22 / 4 | 14,543 / 3,433,698 | 0.0354 / 24.2793 / 24.37 | 20.529436 / 0.88380930 |

任务分别为 `44537f26e3b34c5bb417deacb103ce47`、`c6a40f0953a54a9092430d295e2055a9`。没有未定位照片，完整 SH3 校验通过；用基线代码重新转换各自的原始训练 PLY，与新任务的 PLY / splat 逐字节一致，完整下载和 HTTP Range 内容也一致。

PSNR / SSIM 仅记录新任务的留出输出，没有与其它随机训练模型作质量比较。留出照片仍参与 SfM，不是独立端到端评测。页面、渲染和相机没有改动，且同源模型文件完全一致，没有重复截图或漫游，也未声称修复空洞、漂浮物或细节不足。

34 项转换相关回归、全量 183 项后端、18 项前端、类型检查与生产构建通过。已有 TestClient 弃用提示和约 2.99 MB 查看器构建警告仍在。本地 8787 服务持续可用，无需重启。

## 复现与下一步

```bash
mkdir -p .data/benchmarks/validation-check
git show 8617074:webscan/brush.py > .data/benchmarks/validation-check/baseline.py
.venv/bin/python scripts/benchmark_conversion.py \
  --baseline .data/benchmarks/validation-check/baseline.py \
  --candidate webscan/brush.py \
  --jobs .data/jobs/82acea65e9874f64806815a592015dec \
         .data/jobs/59bb5740d2e945d9a459ca2514c66bb2 \
  --repeats 3 --output .data/benchmarks/validation-check/results
```

输出目录必须不存在。其它机器可替换为保留原始最终 PLY 和报告的已完成任务。完整模型仍会读入内存；每块检查的临时数组受限并不等于整个解析器是流式的。当前收益仅几十毫秒，后续优先回到训练与采集质量的瓶颈，而非继续据此扩大端到端性能宣传。

参考：

- [NumPy 结构化数组](https://numpy.org/doc/stable/user/basics.rec.html) 说明字段布局及缓存访问特点；[isfinite](https://numpy.org/doc/stable/reference/generated/numpy.isfinite.html) 提供逐元素有限值检测。本轮只调整访问顺序，不改变浮点数或输出系数。
- 查阅了 [Brush 当前开发记录](https://github.com/ArthurBrussee/brush/blob/1388f74c6fe0236f68ee4915564bf00e9d2e3747/CHANGELOG.md)：其中融合损失、稀疏缓冲和 Mip-Splatting 仍列于 Unreleased。当前最新正式版本仍为 [0.3.0](https://github.com/ArthurBrussee/brush/releases/tag/v0.3.0)；本轮未升级引擎，也未将上游性能数字计入本地收益。后续需要单独固定源码、依赖与相机测试。
- 照片来自 Schöps 等的 [ETH3D / CVPR 2017](https://www.eth3d.net/datasets)，照片与衍生模型遵循 CC BY-NC-SA 4.0，保留在本地；Brush 保留 Apache-2.0 许可与原作者署名。
