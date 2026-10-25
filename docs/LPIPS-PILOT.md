# LPIPS 感知损失试验

2026-10-25。近期[Apple感知优化研究](https://machinelearning.apple.com/research/drop-in)和[TurboGS](https://proceedings.mlr.press/v306/dong26k.html)都说明感知或结构感知损失可能改善细节，因此验证项目内置的Brush LPIPS路径是否能在本机Metal上运行。

试验只改变 `--lpips-loss-weight`：基线为0，候选为0.01；使用同一个CubeCL尾部修复二进制、pipes固定几何、1000步 / 512像素配置。基线500步先完成，训练和导出耗时4.15秒，生成2,300个高斯。候选在第一组训练的反向阶段失败，没有生成可评分的PLY；因此停止剩余运行。

## 失败原因

Brush 内置LPIPS使用VGG块和五组卷积头，权重已经编译进实验二进制，不需要联网下载。候选日志在Metal自动调优卷积后报错：

```text
BufferTooBig(7795113984)
```

申请的缓冲约7.80 GB，随后Burn Fusion清理阶段又出现索引越界。当前机器有32 GiB内存，但这个单次GPU缓冲请求已经超过本实验运行时可接受的上限；没有修改设备配置或扩大内存来绕过错误。该失败发生在训练反向计算，不是LPIPS画质结论。

## 取舍

不启用LPIPS默认路径，也不把这个失败当作速度或质量结果。需要继续研究时，应先将VGG感知特征分块、降低特征图峰值或使用专门的Metal融合实现，再做真实照片对照；直接把 `lpips_loss_weight` 加到默认档会让部分室内照片无法完成训练。

本轮没有新增应用源码、默认参数或依赖。原始照片和历史模型保留。失败日志留在本机 `.data/benchmarks/lpips-pilot-20261025/`，没有把绝对路径或内部运行记录写入公开JSON。项目仍使用SSIM / L1默认损失，默认SSIM权重为0.2。

参考实现中的LPIPS模块属于Brush实验源的Apache-2.0代码；Apple研究报告WD-R的感知损失结果和TurboGS的结构感知采样只作为后续方向，尚未移植。外部论文结果不换算成本机Metal性能承诺。
