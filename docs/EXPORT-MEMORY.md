# 模型导出内存

2026-10-30。完整 SH3 PLY 和 splat 导出改为直接写入 NumPy 数组，省去 `tobytes()` 对整个数组的临时复制。坐标归一化、旋转、颜色系数、点数和文件格式均保持原样。[实现提交](https://github.com/sakorman/web-scan/commit/a4d83ca) · [逐次数据](benchmarks/export-memory.json)

## 基线与测量

使用历史真实 ETH3D 模型：pipes 300,000 个高斯，office 231,428 个高斯，均由 10,000 步 / 1024 像素训练得到。复用 Brush 的原始最终 PLY 和原任务的归一化参数，转换到新的独立目录，不改写历史工件。

修改前先测 pipes：转换 0.112729 秒，进程峰值 RSS 226,394,112 字节。随后保存基线和候选源码，在每个场景交错执行三次，每次启动新的 Python 进程，共 12 次转换。所有运行锁定源文件 SHA-256，输出 PLY 和 splat 的字节数与 SHA-256 必须完全一致，否则中止比较。

计时仅含读取、校验、归一化、转换和文件写出，不含 Python 导入、摘要检查、SfM 或训练。RSS 是独立转换进程的峰值，包括解释器和导入开销；不是 GPU 显存，也不是整条训练任务的峰值。文件系统缓存没有清空，写入没有额外 `fsync`，因此不代表冷盘或持久化吞吐。

| 真实模型 | PLY / splat 字节 | 峰值 RSS 中位数：基线 → 候选 | 降幅 | 转换耗时中位数 |
| --- | --- | --- | ---: | --- |
| pipes · 300,000 高斯 | 70,801,551 / 9,600,000 | 214.89 → 138.03 MiB | 35.77% | 0.105480 → 0.093490 s |
| office · 231,428 高斯 | 54,618,559 / 7,405,696 | 172.05 → 112.86 MiB | 34.40% | 0.083016 → 0.067461 s |

两组分别少用约 76.86 / 59.19 MiB 进程峰值内存。所有 12 次转换的 PLY 与 splat 内容逐字节一致，文件大小不变。保留此改动的依据是减少冗余复制；毫秒级的转换变化不能写成整条重建流程的加速。

## 真实照片重建回归

随后串行完成两次 Brush 0.3.0 / Metal 重建：同镜头 RADIAL、3,000 步、512 像素、30 万高斯预算、SH3、种子 42、每 8 张留出 1 张。复用上一轮已校验的位姿缓存，输入原始照片均为 1600×1067，实际留出图和评分尺寸均为 512×341。

| 场景 | 输入 / 定位 | 训练 / 留出 | 高斯数 / PLY 字节 | SfM 恢复 / 训练导出 / 总任务秒数 | 任务 PSNR / SSIM |
| --- | --- | --- | --- | --- | --- |
| pipes | 14 / 14 | 12 / 2 | 11,441 / 2,701,626 | 0.0291 / 23.3086 / 23.38 | 18.979542 / 0.78132665 |
| office | 26 / 26 | 22 / 4 | 14,380 / 3,395,230 | 0.0394 / 24.2365 / 24.32 | 21.917908 / 0.89094200 |

没有未定位照片，所有最终 PLY 均通过完整 SH3 检查。再用旧转换代码处理这两份新训练原始 PLY，所得 PLY / splat 与新任务发布的文件逐字节一致；实际下载及 HTTP Range 读取也完全一致。这一校验固定了同一份训练输出，避免将独立训练的随机波动混入导出比较。

表中 PSNR / SSIM 只记录各任务的留出评分，没有宣称画质提高。留出照片参与了上游 SfM，不是独立端到端评测。渲染、相机、训练和页面未变，文件内容又完全一致，本轮没有重复同一模型的截图与漫游检查；已有空洞、拉丝和细节不足不在本改动的解决范围内。

24 项导出及 PLY 校验回归通过；全量 173 项后端、18 项前端、类型检查和生产构建通过。保留已有的 TestClient 弃用提示和约 2.99 MB 查看器模块警告。开始检查时本地服务未运行，重新启动后完成上述任务与下载验证，停止原因尚未确认。

## 复现

从前一提交保存基线，再以当前代码为候选。输出目录必须不存在，以免覆盖历史结果。

```bash
mkdir -p .data/benchmarks/export-check
git show 49eb915:webscan/brush.py > .data/benchmarks/export-check/baseline.py
.venv/bin/python scripts/benchmark_conversion.py \
  --baseline .data/benchmarks/export-check/baseline.py \
  --candidate webscan/brush.py \
  --jobs .data/jobs/82acea65e9874f64806815a592015dec \
         .data/jobs/59bb5740d2e945d9a459ca2514c66bb2 \
  --repeats 3 --output .data/benchmarks/export-check/results
```

任务 ID 是本次本地样本；其它机器可替换为已完成的 Brush 任务目录，须保留 `brush/export_<最终步数>.ply` 和 `artifacts/report.json`。脚本冻结两份适配器，保存逐次测量与输出摘要；支持 macOS 和 Linux 的 RSS 单位差异。

## 范围与参考

本改动只减少主机端文件导出时的临时内存。仍需完整读入模型并构造 splat 数组；高斯数、SH 阶数、GPU 训练显存和下载体积不变。下一步若大型采集仍受内存限制，再分开测量读取、归一化和 splat 构造，决定是否需要分块处理。

- [NumPy `tobytes`](https://numpy.org/doc/stable/reference/generated/numpy.ndarray.tobytes.html) 会构造字节副本；[NumPy `tofile`](https://numpy.org/doc/stable/reference/generated/numpy.ndarray.tofile.html) 支持直接二进制写入。本项目保留已有的小端 `<f4` / `u1` 字段和 PLY 头，不依赖主机默认字节序；只写本地普通文件，不用于压缩流或 `BytesIO`。
- [Efficient Confidence-Aware Densification for 3DGS](https://arxiv.org/abs/2610.13541)，2026-10-09：研究固定点预算下的误差与置信度增密。其点分配收益不能替代导出阶段的独立内存测量，本轮没有移植该算法或将论文结果套用到 Metal。
- [Brush 0.3.0](https://github.com/ArthurBrussee/brush/tree/v0.3.0) 保持原版训练；[3D Gaussian Splatting](https://arxiv.org/abs/2308.04079) 为基础方法。
- 测试照片来自 Schöps 等的 [ETH3D / CVPR 2017](https://www.eth3d.net/datasets)，照片与衍生模型遵循 CC BY-NC-SA 4.0，原图和模型保留本地。
