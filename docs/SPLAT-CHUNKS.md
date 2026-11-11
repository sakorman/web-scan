# 分块生成兼容 splat

2026-11-11。将兼容下载格式的转换限制为每批 32,768 个高斯，减少同时存活的颜色、旋转与输出数组。两份历史实拍模型的独立转换进程峰值 RSS 中位数降低 20%–22%，完整 PLY 和 splat 逐字节相同。采用这一小范围改动；它没有压缩模型，也没有证明训练提速。[逐次测量与新重建记录](benchmarks/splat-chunks.json) · [修改前版本](https://github.com/sakorman/web-scan/commit/8cb69d3)

## 依据与边界

Brush 当前开发记录仍将加载峰值内存优化列在 [Unreleased（1388f74）](https://github.com/ArthurBrussee/brush/blob/1388f74c6fe0236f68ee4915564bf00e9d2e3747/CHANGELOG.md)。Arthur Brussee 的 [PR #325](https://github.com/ArthurBrussee/brush/pull/325/files) 通过减少加载阶段的分配与副本控制内存；本项目采用类似的阶段分析，检查自己的格式转换，未升级或复制上游实现。Brush 保留 Apache-2.0 许可。

Ashim Dahal、Rabab Abdelfattah 与 Nick Rahimi 的近期[系统综述（2026-10，v1）](https://arxiv.org/abs/2610.17513v1)区分已有场景的渲染性能与构建、更新场景的成本。本轮据此只报告被测的转换阶段。可检验假设是：按固定数量的记录生成 splat，可以降低转换峰值内存，同时保持文件完全一致。它与传感器深度、雷达或 CUDA 训练无关；下述真实照片只经 RGB SfM 和本机 Metal 训练。

修改前，完整 PLY 已在内存中，随后还会一次分配整模型的 32 字节 splat 记录以及颜色、透明度和四元数临时数组。修改后保留原有全模型校验和归一化，写完 SH3 PLY 后逐块生成相同的 splat 记录，立即写出并释放块内临时量。每块最终输出最多 1 MiB；中间运算还需要额外内存，**完整 PLY 仍整体载入，整个转换器并非恒定内存**。

## 同源模型对照

先用未修改版本在 300,000 高斯 pipes 模型上建立基线：56.846 ms、峰值 RSS 145,768,448 字节。随后对两份历史原始 Brush PLY 各做三次交错重复，每次启动新进程；这一次单独的预检不混入正式统计。两份模型均来自 10,000 步、1024 像素长边的 ETH3D 任务，定位/输入分别为 14/14 和 26/26，训练/留出为 12/2 和 22/4，无失败视角。

| 模型 | 高斯数 | PLY / splat 字节 | 峰值 RSS 中位数：旧 → 新 | 转换中位数：旧 → 新 |
| --- | ---: | ---: | ---: | ---: |
| pipes | 300,000 | 70,801,551 / 9,600,000 | 138.094 → 107.656 MiB | 49.389 → 42.172 ms |
| office | 231,428 | 54,618,559 / 7,405,696 | 112.906 → 89.922 MiB | 43.930 → 33.517 ms |

RSS 包含进程本身，读取自 macOS 的进程峰值；分别减少 30.438 / 22.984 MiB（22.04% / 20.36%）。计时覆盖读取、校验、归一化、格式转换与写盘，排除导入和摘要计算。系统文件缓存未清空，写盘没有额外 fsync；不属于冷盘或 GPU 内存测量。源文件、归一化参数、输出顺序和所有字段相同，12 次输出的摘要完全一致。

## 新实拍重建与验证

在 Apple M5 上串行运行 Brush 0.3.0 / Metal 两次，复用已校验的共享 RADIAL SfM 相机，新增 SfM 耗时为 0。照片为 1600×1067；种子 42、3,000 步、512 像素训练长边、15 万高斯预算，保留完整 SH3。相机与固定留出参考摘要对齐，原生评分画幅为 512×341。留出照片参与过 SfM，不参加高斯训练。浏览器没有与训练争用 GPU。

| 场景 | 定位/输入；训练/留出 | 高斯 / PLY 字节 | 训练评估导出 / 含准备总耗时 | 留出 PSNR / SSIM |
| --- | --- | ---: | ---: | ---: |
| pipes | 14/14；12/2 | 11,449 / 2,703,514 | 22.904 / 22.964 秒 | 19.079721 dB / 0.78427416 |
| office | 26/26；22/4 | 14,431 / 3,407,266 | 23.696 / 23.773 秒 | 21.893581 dB / 0.89068496 |

用旧版转换器再次转换这两个新任务的原始最终 PLY，两种输出均与新代码逐字节一致，原始文件摘要未变；完整 SH3 读取、相机与留出图身份检查通过。这是导出路径的重建回归，未对两次随机训练的差异宣称质量改善。图像和渲染逻辑未改，文件相同，未重复浏览器漫游或截图；已知管道重影、细线模糊、桌前缘拉伸等问题见[此前同视角图与漫游记录](GMDEPTH-TARGETS.md)，本改动不修复这些缺陷。

新增测试覆盖恰好一个完整块和两个块加一条尾记录，逐条校验位置、缩放、RGBA、WXYZ 顺序，并检查 SH3 与原始文件保留；原有损坏字段测试仍要求在发布任何下载文件前失败。36 项转换相关测试、284 项后端测试、18 项前端测试、类型检查与生产构建通过。仅有既存的 TestClient 弃用和查看器体积警告；8787 健康与历史接口正常。

## 复现与后续

从仓库根目录运行，每次使用新的输出目录：

```sh
mkdir -p .data/benchmarks/splat-chunks-repeat
git show 8cb69d3:webscan/brush.py > .data/benchmarks/splat-chunks-repeat/baseline.py
PYTHONPATH=. .venv/bin/python scripts/benchmark_conversion.py \
  --baseline .data/benchmarks/splat-chunks-repeat/baseline.py --candidate webscan/brush.py \
  --jobs .data/jobs/82acea65e9874f64806815a592015dec \
         .data/jobs/59bb5740d2e945d9a459ca2514c66bb2 \
  --repeats 3 --output .data/benchmarks/splat-chunks-repeat/comparison
PYTHONPATH=. .venv/bin/python docs/benchmarks/gmdepth-controls.py \
  --root .data/benchmarks/splat-chunks-repeat
```

历史任务不可用时，替换为保留原始最终 PLY 与归一化报告的同源任务。照片、原始模型及全部中间测量留在本地；ETH3D 作者 Schöps 等与 CC BY-NC-SA 4.0 许可见[第三方说明](../THIRD_PARTY_NOTICES.md)。本轮没有更改点数、分辨率或数据集来计算收益。下一步若需进一步降低大模型内存，应单独检验 PLY 读取与归一化的流式方案，并保证损坏文件在发布前拒绝；当前实际节省仅为转换进程的数十 MiB。
