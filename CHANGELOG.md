# Changelog

按日期倒序记录每轮改动、验证结果和已知限制。未采用的实验也保留结论，详细配置、截图与测量数据链接到对应报告。历史条目依据提交和当时的测试记录补录，不补造没有测过的结果。

## 2026-10-30

### 缓存任务恢复原始域评测

- 修复：真实 pipes 缓存任务训练成功后，原始域评测却因缺少原始相机失败。现将原始内参随 SfM 场景缓存保存，并为每次任务冻结照片摘要、尺寸和顺序；缓存评测直接校验并读取采集原图，无需额外复制照片或完整稀疏模型。
- 验证：pipes / office 各完成一组 500 步、512 像素的冷启动与缓存 Metal 重建，均为完整定位、SH3 和相机保真，6 张留出视角可在同一原始网格评分；历史两组原始域评分逐项不变。173 项后端、18 项前端、类型检查和构建通过。本轮只修复评测可用性，不把短训练波动写成画质或速度收益。
- 代价与限制：每任务多约 2–4 KB 清单及少量相机参数；旧缓存首次需重新求解，历史缺内参的缓存任务不能追溯补证，原图丢失或被修改时拒绝评分。下一步用缓存路径支持新的重复训练对照。[详细报告](docs/RAW-CACHE-EVALUATION.md) · [实测数据](docs/benchmarks/raw-cache-evaluation.json) · [实现提交](https://github.com/sakorman/web-scan/commit/b0a7ed1) · [COLMAP 格式](https://colmap.github.io/format.html) · [Brush 相机](https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-render/src/camera.rs)

### 跨相机模型原始域评测

- 关键突破：新增 `compare_raw_quality.py`。它验证两次任务的原始照片摘要与留出视角，再用各自保存的 COLMAP 相机把渲染反投影到相同原始像素网格；PSNR / SSIM 只计双方有效区域交集，SSIM 进一步排除不完整的 11×11 窗口。不同去畸变参考图不再被错误地直接横比。
- 验证与取舍：对已完成的 ETH3D pipes / office SIMPLE_RADIAL 与 RADIAL 固定镜头任务复评；两组共同有效区域为 97.0812% / 96.5909%。RADIAL 的均值变化为 pipes +1.577282 dB / +0.05151203 SSIM，office +0.131782 dB / +0.01317179 SSIM；两份任务均保持完整 SH3 和相机工件，并导出同视角原始域图供人工复查。
- 代价与限制：原始域重投影需要逐像素逆畸变与双线性采样，只用于离线对照，不进入重建或网页预览。两场景各只跑一次，office 有两个视角小幅回落，且留出照片参与了上游 SfM；结果只佐证当前固定镜头选择，不能推广到自动、混合镜头、变焦或鱼眼，也不能声称训练加速。下一步在新的固定镜头采集重复配对并再评估外观差异。[完整记录](docs/SHARED-RADIAL.md) · [pipes 原始汇总](docs/benchmarks/shared-radial-raw-pipes.json) · [office 原始汇总](docs/benchmarks/shared-radial-raw-office.json) · [实现提交](https://github.com/sakorman/web-scan/commit/42c4b1f65a9c9a19e0d8de8aeb4f2bffb2da990c) · [COLMAP 投影模型](https://github.com/colmap/colmap/blob/main/doc/cameras.rst) · [3D Gaussian Splatting](https://arxiv.org/abs/2308.04079)

### 同镜头 RADIAL 内参

- 关键突破：明确选择同镜头模式时，COLMAP 从 `SIMPLE_RADIAL` 改为 `RADIAL`，可估计固定镜头的第二个径向畸变系数；自动模式继续使用保守模型，任务报告记录实际 `camera_model`。不改匹配、训练、导出、照片或历史模型。
- 验证与取舍：固定 ETH3D pipes / office 原始照片，以 3,000 步、512 像素、30 万高斯上限、Brush 0.3.0、SH3、种子 42 和留出评估串行完成 SIMPLE_RADIAL 基线与 RADIAL 全流程。两场景均保持 14 / 14、26 / 26 定位，平均重投影误差由 0.9373 / 0.9691 降至 0.5676 / 0.7426 px；pipes 稀疏点 +36，office -28（1.1%）。两份 RADIAL PLY 均含 `f_rest_44`，相机和留出工件有效。
- 代价与限制：相机模型改变了去畸变画幅、相机和留出参考身份，不能横比 PSNR / SSIM；单次 SfM、训练和总耗时也不作提速结论。RADIAL 在少视角、重复纹理或非固定镜头上可能过拟合，故只保留给明确同镜头的照片，默认和混合设备路径保持不变。下一步如需跨内参比较，将渲染投回同一原始相机域，而不复用当前留出分数。[完整记录](docs/SHARED-RADIAL.md) · [公开汇总](docs/benchmarks/shared-radial.json) · [实现提交](https://github.com/sakorman/web-scan/commit/1049b5c90342305c0e8bfe7dc33b3e5fe5fd08e5) · [COLMAP 相机模型](https://github.com/colmap/colmap/blob/main/doc/cameras.rst) · [COLMAP FAQ](https://github.com/colmap/colmap/blob/main/doc/faq.rst) · [近期位姿研究](https://arxiv.org/abs/2508.17876) · [ETH3D](https://www.eth3d.net/datasets)

### 相对重投影残差复查

- 关键突破：质量报告新增“重投影一致性”。它用最终 COLMAP 稀疏点回投每张已定位照片，按照片统计有效观测的平均像素残差；只有高于本次中位数 1.3 倍、且至少有 12 个有效观测的照片才提示人工复查。它不删除照片、不修改位姿、不改 SfM 或训练。
- 验证与取舍：新增高残差识别与少于三张可测视角不输出的回归。固定共享内参、3,000 步、512 像素、30 万高斯上限、SH3、种子 42 和留出评估，对 ETH3D pipes / office 串行完成新 SfM 与 Metal 训练。pipes 14 / 14 定位，中位数 / 提示线 0.9488 / 1.2334 px，无提示；office 26 / 26，中位数 / 提示线 0.9725 / 1.2642 px，仅照片 6 为 1.3281 px（60 点）。两份最终 PLY 都含 `f_rest_44`；浏览器实际确认 office 的新分区、数值与“不自动删图或改变位姿”说明。
- 代价与限制：计算只遍历完成后的有效稀疏观测，未对关闭诊断进行配对测速，不把独立训练的耗时、点数或 PSNR / SSIM 写作优化收益。重复纹理、滚动快门、遮挡和相机模型近似都可能抬高残差；留出照片仍参与上游 SfM。下一步在含已知模糊、滚动快门和补拍前后的合法采集上量化命中率与误报率。[完整记录](docs/REPROJECTION-REVIEW.md) · [公开汇总](docs/benchmarks/reprojection-review.json) · [实现提交](https://github.com/sakorman/web-scan/commit/e457918d2dbf7b387a1d41846867847141321f6b) · [COLMAP 输出格式](https://colmap.github.io/format.html) · [近期位姿研究](https://arxiv.org/abs/2511.06765) · [ETH3D](https://www.eth3d.net/datasets)

## 2026-10-29

### 导出对照锁定留出照片身份

- 关键突破：重复导出基准在汇总前逐项锁定每张留出照片的文件名、参考图 SHA-256 和实际画幅；即使两个任务的训练 / 留出数量相同，只要参考照片不同也会拒绝生成比较结果。渲染图摘要不参与这项身份比对，因此不会把模型输出差异误判为输入不一致。
- 验证与取舍：新增缺失、重复、不完整和“同数量但参考摘要不同”的回归。用两份未改动的 Brush 适配器在冻结 ETH3D pipes / office 上串行完成四次 500 步 / 512 像素控制训练：分别为 12 / 2、22 / 4 训练 / 留出视角，两个变体在各场景的完整参考身份一致；4 个导出均含 SH3 的 `f_rest_44` 字段。短训练的 PSNR / SSIM 有自然波动，只验证评测链路，不作为画质或速度结论。
- 代价与限制：每次汇总多读取少量报告元数据，不改变 SfM、训练或导出；留出照片仍参与上游 SfM，匹配身份不使其成为独立端到端评测。后续新参数对照继续使用此检查，并至少重复两次后再讨论差异。[完整记录](docs/BENCHMARK-IDENTITY.md) · [公开汇总](docs/benchmarks/evaluation-identity.json) · [实现提交](https://github.com/sakorman/web-scan/commit/0344a5cd165d60887a43b66fa1ae2ddf233cdf40) · [3D Gaussian Splatting](https://arxiv.org/abs/2308.04079) · [ETH3D](https://www.eth3d.net/datasets)

### 尺度末段学习率筛查（未采用）

- 关键结论：将 Brush 尺度学习率日程的终点从 `0.006` 降至 `0.003` 后，pipes / office 留出 PSNR 分别下降 1.915650 / 0.074249 dB，SSIM 分别下降 0.01772300 / 0.00918543；默认日程保持不变。
- 验证与取舍：冻结 ETH3D pipes / office 的位姿、照片、留出集合和相机，固定 3,000步、512像素、15万高斯预算、SH3和种子42；两场景交换候选先后顺序，串行完成4次训练。pipes 为14 / 14定位、12 / 2训练/留出，office 为26 / 26、22 / 4；逐张参考摘要与渲染尺寸一致，4个输出均为完整 SH3 PLY。同视角查看显示 pipes 的管道控制器更软、橙墙渗色更重，office 的桌面和前景没有改善。
- 代价与限制：候选少372 / 185个高斯，PLY 少约86 / 43 KiB，但训练、评测和导出仅差 -0.0127 / +0.0303秒，低于一秒轮询精度，不能称为提速。每个设置只跑一次，不能估计方差；留出照片仍参与 SfM。后续只评估可验证的新稳定引擎或明确单变量假设。[完整记录](docs/SCALE-LR-END.md) · [公开汇总](docs/benchmarks/scale-lr-end.json) · [Brush 训练日程](https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-train/src/train.rs#L70-L96) · [3D Gaussian Splatting](https://arxiv.org/abs/2308.04079)

### 高阶 SH 学习率筛查（未采用）

- 关键结论：将 Brush 的高阶 SH 学习率尺度从默认 `20` 调到 `10` 后，两组室内留出图的 PSNR / SSIM 均回退：pipes 为 -0.513525 dB / -0.00691770，office 为 -0.140627 dB / -0.01360470；默认值保持不变。
- 验证与取舍：冻结 ETH3D pipes / office 的位姿、照片、留出集合和相机，固定 3,000 步、512 像素、15 万高斯预算、SH3与种子42；两场景交换候选先后顺序，串行完成 4 次训练。pipes 为14 / 14定位、12 / 2训练/留出，office 为26 / 26、22 / 4；逐张参考摘要与渲染尺寸一致，4 个输出均为完整 SH3 PLY。同视角查看没有改善管线控制器、桌面或前景黑区。
- 代价与限制：候选反而多132 / 246个高斯，最终 PLY 多约31 / 58 KiB；训练、评测和导出仅快 0.0166 / 0.0162 秒，低于一秒轮询精度，不能称为提速。每个设置只跑一次，不能估计方差；留出照片仍参与 SfM。后续只在可验证的新稳定引擎或明确的单变量假设下继续筛查。[完整记录](docs/SH-LEARNING-RATE.md) · [公开汇总](docs/benchmarks/sh-learning-rate.json) · [Brush 0.3.0 配置](https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-train/src/config.rs) · [3D Gaussian Splatting](https://arxiv.org/abs/2308.04079)

### 优先复查视角

- 关键突破：质量报告把“稀疏轨迹支撑偏低”和“图像细节偏低”取严格交集，同一张已定位照片同时命中两项时才列为优先复查对象，并展示轨迹数和细节值。它只缩短人工回看路径：不删图、不改相机位姿、不调整训练或历史模型。
- 验证与取舍：重新导入 ETH3D pipes / office，固定共享内参、3,000 步、512 像素、30 万高斯预算、SH3、种子42和留出评测，串行完成新 SfM 与 Metal 训练。pipes 14 / 14 定位，1 张稀疏支撑偏低但没有低细节视角，交集为空；11,408 高斯、PSNR / SSIM 18.536480 / 0.75678825、29.51 秒。office 26 / 26 定位，6 张稀疏支撑偏低、3 张低细节，交集准确收敛为照片 4–6；14,514 高斯、22.490980 / 0.88416100、32.03 秒。两组完整 SH3、相机、留出图与 PLY / splat 导出均通过，浏览器实际核对 office 的新分区和下载面板。
- 代价与限制：交集仍可能是低纹理墙面而非失焦或坏图；空交集也不能证明采集完整。本轮提示不改 SfM 或训练，不能写作速度或画质提升；留出照片仍参与上游 SfM。后续用已知模糊、曝光变化和补拍后的同场景采集量化优先级的命中率。[完整记录](docs/CAPTURE-REVIEW.md) · [公开汇总](docs/benchmarks/capture-review.json) · [COLMAP 教程](https://colmap.github.io/tutorial.html) · [BAGS](https://arxiv.org/abs/2403.04926) · [3D Gaussian Splatting](https://arxiv.org/abs/2308.04079)

### 相对图像细节诊断

- 关键突破：质量报告新增已定位照片的相对图像细节检查。它把灰度图缩到最长边 512 像素后比较边缘能量，低于本次中位数 25% 的照片才提示复查；不自动删除照片、不改相机、训练参数、模型或历史结果。提示明确保留大面积纯色、失焦、抖动、过曝和重压缩等多种可能，不能将单个分数当作失焦判定。
- 验证与取舍：对 ETH3D pipes / office 以共享内参、3,000 步、512 像素、30 万高斯预算、SH3、种子42和留出评测串行重建；缓存因位姿输出版本变化而重新求解。pipes 14 / 14 定位，细节中位数 / 提示线为 0.006142 / 0.001535、未提示任何照片，10,811 高斯、PSNR / SSIM 18.624418 / 0.75983120、29.31 秒。office 26 / 26，0.004133 / 0.001033，正确提示照片 4–6 的低细节墙面视角，14,611 高斯、21.261750 / 0.88082280、33.00 秒。两组完整 SH3、相机、留出图、PLY 和 splat 均有效；浏览器实际打开报告和下载面板，确认文字、模型文件及大小正确。
- 代价与限制：这不是清晰度绝对分数，也没有关闭诊断的配对测速，不能宣称画质或速度变化；留出照片仍参与 SfM。下一步用已知移动模糊与曝光变化的合法采集量化命中率和误报率。[完整记录](docs/IMAGE-DETAIL.md) · [公开汇总](docs/benchmarks/image-detail.json) · [Tenengrad 边缘能量说明](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=890060) · [BAGS](https://arxiv.org/abs/2403.04926) · [3D Gaussian Splatting](https://arxiv.org/abs/2308.04079)

### 原地旋转提示

- 关键突破：把已有相机中心重合判定接入任务警告和质量报告。若至少 3 张已定位相机中心重合，会明确提示“仅转动镜头没有三维深度所需视差”，建议围绕目标小幅横移；不改变 COLMAP、训练、照片、模型或历史结果。
- 验证与取舍：矩阵回归保留少视角不判断、三张中心重合的退化路径。ETH3D pipes / office 均以 SfM 缓存复用、共享内参、3,000 步、512 像素、30 万高斯预算、SH3、种子42和留出评测串行完成：pipes 14 / 14 定位，10,840 高斯、PSNR / SSIM 18.994553 / 0.76057780、23.35 秒；office 26 / 26，14,617 高斯、22.210938 / 0.88326170、23.32 秒。两组相机中心均不重合，没有误报；完整 SH3、相机、留出图和 PLY 有效，浏览器实际复查质量报告。
- 代价与限制：这只显示已有判定，未和关闭提示做配对测速，不能宣称速度或画质变化。中心不重合也不能证明基线、重叠、遮挡、纹理和全表面覆盖；留出照片仍参与 SfM。下一步以含已知原地旋转段的合法采集量化命中率和误报率。[完整记录](docs/PARALLAX.md) · [公开汇总](docs/benchmarks/parallax.json) · [COLMAP 采集指导](https://colmap.github.io/tutorial.html) · [3D Gaussian Splatting](https://arxiv.org/abs/2308.04079)

## 2026-10-28

### 拍摄朝向诊断

- 关键突破：质量报告新增相机光轴两两夹角分布。仅在至少 3 张已定位照片且夹角 p90 小于 20° 时提示“大多数相机朝向接近”；它与位置间隔、稀疏轨迹独立呈现，不动相机求解、训练参数、模型和历史任务。
- 验证与取舍：固定 ETH3D pipes / office 的公开照片、共享内参、3,000 步、512 像素、30 万高斯预算、SH3、种子42和留出评测，串行重建两次。pipes 14 / 14 定位，朝向中位 / p90 / 最大为 71.5° / 131.7° / 177.0°，11,104 高斯、PSNR / SSIM 18.612213 / 0.76559970、29.33 秒；office 26 / 26，68.1° / 120.2° / 168.2°，14,421 高斯、21.542286 / 0.87124693、32.69 秒。两组完整 SH3、相机、留出图和 PLY 有效；浏览器实际核对 office 质量报告正确显示新分区及范围说明。
- 代价与限制：本轮没有关闭诊断的配对测速，不能宣称性能或画质变化。朝向分散不等于位置、视差、遮挡和所有表面都已覆盖；留出照片仍参与 SfM，指标不是独立端到端评测。下一步在已知单朝向与环绕采集场景上量化命中率和误报率。[完整记录](docs/POSE-DIRECTION.md) · [公开汇总](docs/benchmarks/pose-direction.json) · [COLMAP 相机模型](https://colmap.github.io/cameras.html) · [3D Gaussian Splatting](https://arxiv.org/abs/2308.04079) · [SPFSplatV2](https://arxiv.org/abs/2509.17246)

### 辅助约束结束时刻筛查（未采用）

- 关键结论：将 Brush 透明度、尺度辅助约束的结束点从训练进度 `0.9` 对齐到增点停止的 `0.8`。pipes 的共同512网格均值 PSNR / SSIM 提高 0.676409 dB / 0.004467，但高斯数增加 5.08%；office 同时下降 0.373920 dB / 0.006722，且高斯数增加 1.31%。
- 验证与取舍：固定 ETH3D pipes / office 的相机、输入、留出照片、3,000步、512像素、15万高斯预算、SH3和种子42，以相反顺序各重复一次，完成8次串行训练。完整 SH3 PLY、相机、参考摘要和共同网格均通过校验；office 第二轮办公桌视角出现前景发黑与缺失，默认参数不改动。
- 代价与限制：训练、留出评测与导出均值差为 -0.03 s / +0.01 s，低于轮询精度，不能称为提速。两次重复只足以排除本候选；留出照片仍参与 SfM。[完整记录与截图](docs/AUX-LOSS-STOP.md) · [公开汇总](docs/benchmarks/aux-loss-stop.json) · [Brush 训练实现](https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-train/src/train.rs#L143-L199) · [SteepGS](https://openaccess.thecvf.com/content/CVPR2025/html/Wang_Steepest_Descent_Density_Control_for_Compact_3D_Gaussian_Splatting_CVPR_2025_paper.html) · [AD-GS](https://doi.org/10.1145/3757377.3763993)

### 增点梯度阈值筛查（未采用）

- 关键结论：借鉴增密控制研究，将 Brush 的高梯度扩张阈值由 `4e-5` 提到 `8e-5`，两组模型约少 20% 高斯，但没有得到稳定的同画质提速。pipes 两次共同512网格均值 PSNR / SSIM 下降 0.291553 dB / 0.012075；office PSNR 增 0.312880 dB、SSIM 降 0.001683。
- 验证与取舍：冻结同一组 ETH3D 位姿、照片、留出视角和参考图摘要，以相反顺序各跑两次真实 GPU 训练。pipes / office 分别为 14 / 14、26 / 26 定位，完整 SH3、相机和评测图均通过；第二轮 pipes 候选在同视角出现明显管线变软与墙面渗色。默认参数和历史模型均不改动。
- 代价与限制：候选在 pipes 平均 23.6967 秒，和 23.6935 秒基线没有实际时延收益；office 约快 0.50 秒，但画质方向不一致。两次重复只足够排除该候选，不能概括其它阈值或场景；评估照片仍参与 SfM。[完整记录与截图](docs/GROWTH-THRESHOLD.md) · [公开汇总](docs/benchmarks/growth-threshold.json) · [Brush 配置](https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-train/src/config.rs) · [原始3DGS增密参数](https://github.com/graphdeco-inria/gaussian-splatting#--densify_grad_threshold) · [Adaptive Density Control](https://arxiv.org/abs/2503.14274)

### 拍摄位置间隔诊断

- 关键突破：质量报告新增已定位相机的最近邻位置间隔提示。它在场景归一化尺度内计算中位数，并以 2.5 倍中位数标出相对孤立的位置；不依赖照片顺序、不改变相机、训练、模型或历史任务，与稀疏轨迹支撑分开呈现。
- 验证与取舍：新增矩阵、重合中心和少视角回归。固定共享内参、3,000 步、512 像素、15 万高斯预算、SH3、种子42和留出评测，串行重新完成 ETH3D pipes / office 两次真实任务：pipes 14 / 14 定位，`0007…` 间隔 0.255536（线 0.203706），11,180 高斯、PSNR / SSIM 18.814209 / 0.76881766、29.23 秒；office 26 / 26 定位，5 张相对孤立，14,605 高斯、22.289450 / 0.88654310、31.98 秒。两组完整 SH3、相机、留出图和 PLY 有效。
- 代价与限制：归一化距离不能跨项目比较，也不包含朝向、视场、遮挡、纹理或真实重叠；大间隔不必然是问题，小间隔也不能保证视差。独立 Brush 训练不可作为画质或速度提升结论，留出照片仍参与上游 SfM。下一步在连续手机采集和已知缺口场景核对提示的实际命中率。[完整记录与复现](docs/POSE-SPACING.md) · [公开汇总](docs/benchmarks/pose-spacing.json) · [基线](https://github.com/sakorman/web-scan/commit/5743261) · [COLMAP 拍摄指导](https://colmap.github.io/tutorial.html) · [COLMAP 相机模型](https://colmap.github.io/cameras.html) · [SPFSplatV2](https://arxiv.org/abs/2509.17246)

## 2026-10-27

### 高像素照片安全归一化

- 关键突破：移动端原图不再在 2400 万像素处提前拒绝；服务端现接受不超过 4800 万像素的图片，在完整解码前检查尺寸，再按原有 2400 像素长边规范化。有效 35mm 等效焦距继续保留，项目中会记录源图与最终尺寸；16 MiB 单文件、200 张 / 512 MiB 项目容量没有放宽。
- 验证与取舍：6000×5000（3000 万像素）边界图完成完整上传，得到 2400×2000 输出、焦距 28 和可追溯元数据；另有缩小阈值的缩放、元数据和超限拒绝回归。重新导入 ETH3D 实拍并串行跑完 3,000 步 / 512 像素 / 15 万高斯共享内参任务：pipes 14 / 14 定位，10,903 高斯、PSNR / SSIM 18.793522 / 0.760213、29.13 秒；office 26 / 26 定位，14,323 高斯、21.803417 / 0.87744385、31.84 秒。两组完整 SH3、相机、留出图与 PLY 均有效。
- 代价与限制：48MP 是解码安全边界，不代表更高像素一定改善位姿或画质；本轮 ETH3D 输入已是 1600×1067，只能确认普通导入与重建未回退，不能把独立 Brush 运行当作画质或速度提升。留出照片参与过上游 SfM，下一步补充真实手机高像素、不同方向和编码的内存测试。[完整记录与复现](docs/HIGHRES-INGEST.md) · [公开汇总](docs/benchmarks/highres-ingest.json) · [基线](https://github.com/sakorman/web-scan/commit/e64fb47) · [Pillow 像素安全限制](https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.MAX_IMAGE_PIXELS) · [COLMAP 相机模型](https://colmap.github.io/cameras.html) · [3D Gaussian Splatting](https://arxiv.org/abs/2308.04079)

### 稀疏视角支撑诊断

- 关键突破：质量报告新增已定位照片的稀疏轨迹支撑提示。它只统计轨迹长度不少于 2、重投影误差低于 4 px 的点，并按当前任务中位数设置 64～300 的相对提示线；不是模型质量分数，也不改动相机、训练参数或历史模型。
- 实测与取舍：固定共享内参、3,000 步、512 像素、15 万高斯预算、SH3、种子42和留出评测，串行完成 ETH3D pipes / office 两次真实 Metal 任务。pipes 全部定位 14 / 14，其中 1 张照片为 111 / 113；office 全部定位 26 / 26，其中 5 张为 63～112 / 122。两次完整 SH3、相机和留出输出均有效；任务分别为 29.07、32.90 秒。诊断不声称画质或提速，留出照片仍参与上游 SfM。
- 代价与限制：低支撑可能是小范围或无纹理区域，高支撑也不能证明表面覆盖完整；它只提供补拍/复查线索。7 项聚焦回归、147 项后端、18 项前端、类型和生产构建通过；浏览器实际验证报告、关闭、拖动和桌面/手机布局，未恢复方向按钮或悬浮提示。后续在连续手机采集、反光材质和混合镜头照片上验证提示的有效性。[完整数据与复现](docs/SPARSE-COVERAGE.md) · [公开汇总](docs/benchmarks/sparse-coverage.json) · [基线](https://github.com/sakorman/web-scan/commit/dca4c3f) · [COLMAP 教程](https://colmap.github.io/tutorial.html) · [SparseSurf](https://ojs.aaai.org/index.php/AAAI/article/view/36531)

### 低透明度均值噪声筛查（未采用）

- 关键结果：仅将 Brush 对低透明度高斯的均值噪声从 `40` 降到 `20` 后，pipes 的共同512网格 PSNR / SSIM 增加 1.03641 dB / 0.018275，但 office 下降 0.56947 dB / 0.007398；不以单场景收益替换默认参数。
- 实测与取舍：固定 ETH3D 位姿、输入摘要、留出照片、3,000步、512像素、15万高斯预算、SH3 和种子42，串行完成两场景各一组真实 Metal 训练，只切换均值噪声。四个输出通过完整SH3、相机、留出照片和共同网格复评；候选仅少 0.4% / 1.9% 高斯，耗时无显著差异，既不是稳定压缩也不是提速。上游将这类噪声保持到训练末尾，当前证据不足以削弱默认值。后续优先评估采集覆盖或可验证的几何约束。[完整记录、截图与复现](docs/MEAN-NOISE.md) · [公开汇总](docs/benchmarks/mean-noise.json) · [基线](https://github.com/sakorman/web-scan/commit/0efaaef) · [Brush 0.3.0 配置](https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-train/src/config.rs) · [上游改动](https://github.com/ArthurBrussee/brush/commit/9fc23abdbdc99cfee1bebb31197d6927a0c89c7c) · [3D Gaussian Splatting](https://arxiv.org/abs/2308.04079)

### 高梯度扩张比例筛查（未采用）

- 关键结果：将 Brush 每轮细化的高梯度扩张比例从 `0.1` 提到 `0.2`，pipes / office 的共同512网格 SSIM 增加 0.00860 / 0.01325，但 PSNR 分别下降 0.25367 / 0.76810 dB。候选分别多出 365.32% / 278.26% 的高斯，训练、评测和导出慢约 21.7%，不作为默认质量档。
- 实测与取舍：固定 ETH3D 相机、输入摘要、留出照片、3,000步、512像素、15万高斯预算、SH3 和种子42，只切换扩张比例；两场景各完成一组串行训练、完整SH3检查与共同网格复评。pipes 的单张留出图里斜向漂浮线条较少，但全视角 PSNR 与模型代价均不支持接入。后续应优先改善拍摄覆盖或引入有验证的几何监督，而不是单纯扩张更多高斯。[完整记录、截图与复现](docs/GROWTH-SELECT.md) · [公开汇总](docs/benchmarks/growth-select.json) · [基线](https://github.com/sakorman/web-scan/commit/b16428b) · [Brush 0.3.0 细化实现](https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-train/src/train.rs#L398-L432) · [AD-GS](https://doi.org/10.1145/3757377.3763993)

### 透明度正则筛查（未采用）

- 关键结果：将 Brush 原始 opacity 正则从 `1e-9` 提到 `1e-7` 后，pipes / office 的共同512网格 SSIM 分别增加 0.00289 / 0.00421，但 PSNR 分别下降 0.63059 / 0.02513 dB；pipes 的管道接头与门板边界更软。默认权重不变，不把更小 PLY 或近似相同用时写成优化。
- 实测与取舍：固定 ETH3D 已定位相机、输入摘要、留出照片、3,000步、512像素、15万高斯预算、SH3 和种子42，只切换透明度权重；两场景各完成一组串行训练、原生留出评测、完整SH3检查和共同网格复评。候选高斯数少 10.06% / 6.72%，时间差不足 0.01 秒，无法抵消画质退化。后续优先从采集覆盖和位姿约束排查漂浮物，而不是继续提高这一单项权重。[完整记录、截图与复现](docs/OPACITY-LOSS.md) · [公开汇总](docs/benchmarks/opacity-loss.json) · [基线](https://github.com/sakorman/web-scan/commit/fd092ee) · [Brush 0.3.0 训练实现](https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-train/src/train.rs#L187-L205) · [EFA-GS](https://arxiv.org/abs/2508.02493)

### 200 步增点间隔筛查（未采用）

- 关键结果：应用原有的 100 步细化改为 Brush 默认的 200 步后，pipes / office 的训练、留出评测和导出各少约 1 秒，但高斯数减少 65.47% / 64.09%。统一512评分的 PSNR 分别下降 0.41125 / 0.30482 dB，SSIM 下降 0.02123 / 0.01876；细管、接头和门板线条也更软。默认仍为 100 步，不把更小模型或单次用时当作同画质提速。
- 实测与改动：固定已定位的 ETH3D 位姿、输入摘要和留出图，3,000 步、512 像素、15万高斯预算、SH3、种子42，只切换细化间隔；pipes按100→200、office按200→100串行运行。导出基准补齐普通任务的场景、相机和报告工件，评分脚本会先校验相机、留出照片和参考摘要，再以双方原生512网格复评。32项导出 / 评测相关测试通过；4个模型均通过完整SH3校验。
- 代价与限制：每个场景只有一组配对筛查，不能估计一般性能；留出照片参与过上游SfM，也不是独立端到端评测。第一轮候选包装器误拦截了版本读取，没有产出候选模型；修正后以新目录完成四次训练，未把不完整首轮混入结果。后续优先从采集覆盖和位姿质量入手，而不是继续扩大细化间隔。[完整记录、截图与复现](docs/REFINEMENT-200.md) · [公开汇总](docs/benchmarks/refine-every-200.json) · [基线](https://github.com/sakorman/web-scan/commit/1564b8c) · [Brush 0.3.0 配置](https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-train/src/config.rs) · [3D Gaussian Splatting](https://arxiv.org/abs/2308.04079)

### 已知焦段冲突不再自动共享内参

- 关键突破：上传和样本准备会保留可用的 35mm 等效焦距。自动模式定位不足时，只有照片尺寸一致且所有已知焦距一致才尝试同镜头内参；缺失焦距不改变既有流程。报告同步记录已知焦距集合与未重试原因，避免同尺寸变焦照片被自动合并。
- 验证：office 无焦距提示的 26 张实拍复跑仍为 20 / 26 自动定位，随后以同镜头候选恢复至 26 / 26、2,497 稀疏点，完整 SfM 为 19.3354 秒。另做压缩像素不变、仅写入 24 mm / 48 mm 提示的副本，自动模式已直接定位 26 / 26，因定位率充足未进入共享分支；低定位率冲突分支由回归测试覆盖。144 项后端、18 项前端、类型检查和构建通过，本地服务已重启。未重跑 GPU 训练，不把该位姿保护写成画质或速度收益。[完整记录与复现](docs/CAMERA-FALLBACK.md) · [公开汇总](docs/benchmarks/camera-fallback.json) · [前序回退实现](https://github.com/sakorman/web-scan/commit/a3fec38)
- 代价与限制：检查只读取已保存的最小 EXIF 焦距字段，不能识别缺失或错误元数据；变焦、双摄或混合设备仍应保持自动模式或手动选择。依据 [COLMAP 相机模型](https://github.com/colmap/colmap/blob/main/doc/cameras.rst) 对共享内参的适用边界，后续需要用真实混合镜头采集补充覆盖。

## 2026-10-26

### 自动内参的保守回退

- 关键突破：同尺寸的 ETH3D office 照片在自动内参下只定位 20 / 26 张；同镜头重试定位 26 / 26 张，稀疏点 1,806 → 2,501，平均重投影误差仅 0.95299 → 0.97011 px。自动模式现在只在同尺寸且定位率低于 90% 时重试；重试还必须同时增加定位数、稀疏点，并把误差控制在首轮的 110% 内，才会采用。
- 实测：office 完整位姿阶段为 19.2353 秒（首轮 11.0070 秒加重试 7.9977 秒），随后 3000 步 / 512 像素留出训练完成，生成 14,560 个高斯和 3.28 MiB 完整 SH3 PLY。评分为 PSNR 21.30112、SSIM 0.87967，但留出视图由 3 张变为 4 张，不能与旧自动结果直接比较。pipes 已定位 14 / 14，没有触发重试。质量报告会显示实际采用的相机模式与两轮定位数。[完整记录、数据和复现](docs/CAMERA-FALLBACK.md) · [公开汇总](docs/benchmarks/camera-fallback.json) · [实现](https://github.com/sakorman/web-scan/commit/a3fec38) · [前序基线](https://github.com/sakorman/web-scan/commit/a3874cc)
- 代价与限制：这用额外的 SfM 时间换取覆盖，不是提速；同尺寸也不能证明未变焦。共同拍摄视角仍有墙面、显示器边缘发软和轻微漂浮亮斑，下一步用精细档和更多不同镜头的实拍复核。参考 [COLMAP 相机模型](https://github.com/colmap/colmap/blob/main/doc/cameras.rst)、[COLMAP 教程](https://github.com/colmap/colmap/blob/main/doc/tutorial.rst) 与 [3D Gaussian Splatting](https://arxiv.org/abs/2308.04079)。

### 顺序匹配筛查（未采用）

- 关键结果：连续 TUM desk 的 SfM 从 34.1641 秒降到 21.3348 秒，96 张照片仍全部定位；但 3000 步留出 PSNR / SSIM 从 23.11717 / 0.84490 降到 22.85436 / 0.84011，且高斯数减少 13.94%。
- ETH3D pipes / office 的匹配时间也变短，却分别少定位 3 / 2 张照片，稀疏点减少 49.54% / 52.88%。默认仍用穷举匹配；顺序方式只保留为有序连续照片的显式筛查工具，不影响既有任务或模型。[基线](https://github.com/sakorman/web-scan/commit/b82376d) · [完整记录与复现](docs/SFM-SEQUENTIAL.md) · [公开汇总](docs/benchmarks/sfm-sequential.json)
- 全量回归先发现无效参数会在校验前加载原生 PyCOLMAP；已改为先拒绝无效方式或重叠范围。顺序匹配、图像顺序和缓存 11 项聚焦回归，以及 141 项后端、18 项前端、类型检查和构建均通过。
- 参考 [COLMAP 配对说明](https://github.com/colmap/colmap/blob/main/doc/tutorial.rst) 与 [PyCOLMAP 顺序配对 API](https://colmap.github.io/legacy/3.13/pycolmap/pycolmap.html)。一次运行不足以下结论；后续需在更多连续采集和重复中验证定位完整性。

### 限制评分到共同原生尺寸

- 关键修复：128像素渲染不再因请求512而被隐式放大。报告同时记录请求上限、共同原生上限和逐视角尺寸，处理短边舍入及首张照片较小的旧报告；尺寸不足时明确报错。
- 真实128像素对照已复现旧行为，修复后回到原生尺寸；已有512像素pipes / office的像素摘要与三项指标保持一致，142个原文件不变。136项后端、18项前端、类型与构建通过。本地服务已恢复，历史模型与下载入口正常。[基线](https://github.com/sakorman/web-scan/commit/9a20934) · [数据、图示与限制](docs/SCORING-SIZE.md)
- 参考[3DGS评测实现](https://github.com/graphdeco-inria/gaussian-splatting/blob/main/metrics.py)和[LPIPS](https://github.com/richzhang/PerceptualSimilarity)。这是评分尺度修正，不是模型画质或训练提速；旧成绩保留，后续基线不混用两种尺度。

### 长训练模型的感知复查 · [9a20934](https://github.com/sakorman/web-scan/commit/9a20934)

- 关键结果：给已有12个6000步模型补齐72对LPIPS评分。office平均0.28580 → 0.29160（低为好），8组视角与重复对照中7组变差，与PSNR / SSIM下降一致；pipes均值改善，TUM变化小，默认接入结论保持。
- 约53秒完成补评分，760个原始文件摘要前后一致，原PSNR / SSIM完整复现。复查office四个模型及两个候选漫游，正常加载；直接复用已有截图，没有重新训练。同步更正旧文档的pipes评分口径、模型总数和论文链接。[基线](https://github.com/sakorman/web-scan/commit/9083b95) · [数据、截图与局限](docs/PERCEPTUAL-LONG.md)
- 参考[LPIPS](https://arxiv.org/abs/1801.03924)与[2026年感知优化论文](https://arxiv.org/abs/2603.23297)。两次重复不作显著性结论；保留联合指标基线，不为追求通过而重复同一实验。

### 增加留出图感知评分 · [9083b95](https://github.com/sakorman/web-scan/commit/9083b95)

- 关键进展：对照脚本可选离线LPIPS-VGG，和PSNR / SSIM使用相同RGB像素；记录评分器、权重及处理后图片摘要，拒绝缺失 / 重复 / 无效分数，相机或留出参考不一致时先报错。
- 新跑两组500步默认损失，复用此前感知损失模型。pipes / office的LPIPS为0.65909 → 0.59806、0.49388 → 0.48158（低为好），但只有单次短训练，且训练与评分共用VGG，默认LPIPS仍关闭。127项后端、18项前端、类型和构建通过；四个模型加载、两个新模型漫游正常。[基线](https://github.com/sakorman/web-scan/commit/594134f) · [成绩、成本与截图](docs/PERCEPTUAL-EVAL.md)
- 参考[LPIPS论文](https://arxiv.org/abs/1801.03924)、[官方实现](https://github.com/richzhang/PerceptualSimilarity)及[近期感知优化研究](https://machinelearning.apple.com/research/drop-in)。保留评测工具，下一步先给已有长训练结果补分，避免重复重建。

### 冻结 LPIPS 预训练参数（实验修复） · [594134f](https://github.com/sakorman/web-scan/commit/594134f)

- 关键进展：查出感知网络权重被无用求导，加载后冻结18组权重和13组偏置，保留预测图梯度。512像素的pipes / office均完成500步重建，此前pipes会申请约7.80GB缓冲并失败，不需要降低输入分辨率。
- 参数回归先失败后通过，3项GPU测试通过，所测LPIPS分数相同、预测梯度最大差1.75e-10。六次重建、完整SH3、14张留出图、四个模型加载及三个候选漫游通过；补丁在干净源码副本中复现一致。[基线](https://github.com/sakorman/web-scan/commit/c7d2a2b) · [实测、补丁与截图](docs/LPIPS-FROZEN.md)
- 同样启用LPIPS的128像素对照平均56.43 → 37.33秒，减少约34%，但平均PSNR / SSIM下降；512像素仍需333～358秒，默认LPIPS保持关闭。保留计算修复，下一步验证感知质量与成本。[LPIPS论文](https://arxiv.org/abs/1801.03924) · [官方冻结特征网络实现](https://github.com/richzhang/PerceptualSimilarity/blob/082bb24f84c091ea94de2867d34c4544f68e0963/lpips/pretrained_networks.py)

## 2026-10-25

### LPIPS 低分辨率资源边界（未采用） · [c7d2a2b](https://github.com/sakorman/web-scan/commit/c7d2a2b)

- 关键结果：将LPIPS目标长边从512降到128后可以完成训练，但pipes 500步平均耗时从2.11秒增至71.99秒，PSNR下降0.450 dB、SSIM下降0.00366；低分辨率结果不用于质量结论，也不启用默认LPIPS。
- 四次真实照片重建、完整SH3、两个模型浏览器加载和拖动漫游通过；确认512失败是资源峰值问题，下一步应做特征分块或Metal内存控制，而不是降低产品分辨率。[完整记录、论文和截图](docs/LPIPS-LOWRES.md)

### LPIPS 感知损失试验（未采用）

- 关键结果：项目内置LPIPS在Apple Metal第一组候选反向阶段申请约7.80 GB缓冲，触发 `BufferTooBig`，没有生成候选模型；基线500步正常完成约4.15秒。停止扩大试验，不把它写成画质结论。
- 记录了失败边界、内置权重和资源代价，默认仍使用SSIM / L1，原照片和历史模型不变。下一步若继续研究需先做VGG特征分块或降低峰值显存。[完整记录](docs/LPIPS-PILOT.md)
- 参考[Apple感知优化研究](https://machinelearning.apple.com/research/drop-in)和[TurboGS](https://proceedings.mlr.press/v306/dong26k.html)。论文中的WD-R、稀疏采样和结构感知损失没有直接移植到本地Metal训练器。

### SSIM 权重短对照（未采用）

- 关键结论：将现有 `ssim_weight` 从0.2提高到0.3，pipes平均SSIM提高0.00862但PSNR下降0.2823 dB，超过筛查门槛；office两项指标通过。默认仍保持0.2，不把单一SSIM提升当作整体画质收益。
- 同一尾部修复二进制完成pipes / office八次1000步对照，完整SH3、四个模型加载和拖动漫游通过；没有新增源码或默认参数改动。[基线](https://github.com/sakorman/web-scan/commit/13a1a2a) · [结果、论文和截图](docs/SSIM-WEIGHT.md)
- 参考[Apple感知优化研究](https://machinelearning.apple.com/research/drop-in)和[TurboGS](https://proceedings.mlr.press/v306/dong26k.html)。它们使用更完整的感知损失或稀疏采样方案，本轮没有移植；下一步若继续研究需加入LPIPS / DISTS和人眼对照。

### 张量尾部修复的默认质量档复核（未采用）

- 关键结论：修复后6000步 / 1024目标长边三场景12次对照中，pipes和TUM通过筛查，office平均PSNR下降0.264 dB、SSIM下降0.00760，超过预设门槛；三场景耗时均略慢，不接入默认引擎。
- 补齐Brush与CubeCL依赖覆盖、Cargo锁文件摘要、完整SH3、72,000条消费记录和六张同视角原生图片。浏览器加载及拖动漫游正常；保持官方Brush、原照片和历史模型不变。[基线](https://github.com/sakorman/web-scan/commit/442b49a) · [完整默认档报告](docs/SSIM-TAIL-DEFAULT.md)
- 继续参考[SSIM论文](https://ece.uwaterloo.ca/~z70wang/publications/ssim.pdf)、[CubeCL实现](https://github.com/ArthurBrussee/cubecl/blob/43580a8c294f3a8140921c26f6378a12aef82881/crates/cubecl-std/src/tensor/contiguous.rs)和[Faster-GS](https://fhahlbohm.github.io/faster-gaussian-splatting/)。该修复解决已复现的输入漏写，但没有被证据支持为整体质量或速度提升。

### SSIM输入尾部复制修复（实验）

- 关键突破：把626×469异常缩小到锁定CubeCL连续复制的向量尾部；修复打包向量宽度必须整除总元素数后，624～628宽度全部无漏写，未填充的TUM组件探查恢复一致。
- 组件回归、6项SSIM / 梯度测试、三场景12次短重建、完整SH3和浏览器拖动漫游通过；短训练没有一致画质或速度收益，正式Brush不替换，也没有修改照片或相机。保留修复前后证据与下一步长档复核。[基线](https://github.com/sakorman/web-scan/commit/7abc8f2) · [完整记录、补丁与截图](docs/SSIM-TAIL.md)
- 参考[SSIM论文](https://ece.uwaterloo.ca/~z70wang/publications/ssim.pdf)、[CubeCL连续复制实现](https://github.com/ArthurBrussee/cubecl/blob/43580a8c294f3a8140921c26f6378a12aef82881/crates/cubecl-std/src/tensor/contiguous.rs)和[Faster-GS](https://fhahlbohm.github.io/faster-gaussian-splatting/)。只采用输入写入修复，不把短训练分数写成整体质量提升。

### 参考二阶矩与尺寸边界（实验）

- 关键进展：改存参考二阶矩后，五组实拍 / 尺寸组合的SSIM图及预测梯度与原公式一致；同时查出626×469输入的局部重复性异常，原路径也会出现，补两列的组件对照恢复一致，根因尚待定位。
- 两项缓存回归、release构建、八次1000步重建与浏览器拖动漫游通过。第一次参数更新后仍有运行差异，画质没有一致提升；保留实验开关，暂缓长训练和默认接入。补列没有用于训练照片或相机。[基线](https://github.com/sakorman/web-scan/commit/63a4fdd) · [数据、截图与下一步](docs/SSIM-MOMENTS.md)
- 参考[SSIM论文](https://ece.uwaterloo.ca/~z70wang/publications/ssim.pdf)、[fused-ssim Metal实现](https://github.com/rahul-goel/fused-ssim/blob/a7c48d6dd7ac6dc39a7958c7c4452e0b10418f38/ssim.mm)及[Faster-GS](https://fhahlbohm.github.io/faster-gaussian-splatting/)。未移植CUDA增点策略，未把局部数值改善写成整体画质或速度提升。

### 参考统计缓存的默认档复核（未采用） · [63a4fdd](https://github.com/sakorman/web-scan/commit/63a4fdd)

- 关键结论：6000步、1024目标分辨率的三场景12次对照仍较快，但pipes平均PSNR下降0.480 dB、SSIM下降0.01120，超过事先设定的门槛，不启用默认缓存。office与TUM通过本轮筛查，不能据此保证其他场景不退化。
- 沿用同一实验引擎，只切换缓存；保留72,000条追踪、完整SH3、同视角成绩和8张原生渲染图。浏览器加载及拖动漫游正常，缓存数据为189～255 MiB；未重测SfM和端到端耗时。下一步单独检验参考方差的运算分组，尚无已验证修复。[基线](https://github.com/sakorman/web-scan/commit/305e826) · [完整记录与截图](docs/SSIM-REFERENCE-DEFAULT.md)
- 参考[SSIM论文](https://ece.uwaterloo.ca/~z70wang/publications/ssim.pdf)、[fused-ssim](https://github.com/rahul-goel/fused-ssim)和[Faster-GS论文](https://fhahlbohm.github.io/faster-gaussian-splatting/assets/hahlbohm2026fastergs.pdf)。耗时下降没有通过全部画质检查，未作为同画质提速发布。

## 2026-10-24

### 复用SSIM参考统计量（实验） · [305e826](https://github.com/sakorman/web-scan/commit/305e826)

- 关键进展：固定3000步、512像素，三场景12次对照的训练、评估与导出平均少约2秒，降幅约5%～9%。参考照片的均值和方差按图像路径及尺寸缓存，256 MiB上限外回到原计算；本轮缓存数据为47～252 MiB。
- 数值 / 梯度、容量和需梯度参考图回退检查通过，完整SH3及浏览器漫游正常。pipes平均SSIM略降，尚未确认质量不退化；保留实验开关，先验证默认质量档，再决定替换正式引擎。[基线](https://github.com/sakorman/web-scan/commit/088494e) · [数据、截图与代价](docs/SSIM-REFERENCE.md)
- 参考[SSIM原论文](https://ece.uwaterloo.ca/~z70wang/publications/ssim.pdf)、[fused-ssim](https://github.com/rahul-goel/fused-ssim)和[Faster-GS](https://github.com/nerficg-project/faster-gaussian-splatting)。本轮复用固定参考项，未移植CUDA或MPS融合内核。

### SSIM统计合批（未采用） · [088494e](https://github.com/sakorman/web-scan/commit/088494e)

- 关键结论：五组统计量合成两批后，数值和梯度检查通过，但Metal组件计时中位数慢了13.1%～15.4%，不启用默认。前向高层卷积调用减少，不等于底层执行更快。
- 保留原始基线运算顺序，排除早期重排基线的探查。3项测试、release构建、四次真实照片短重建与浏览器漫游检查通过；取消无收益的长训练对照，正式引擎和历史模型保留。[基线](https://github.com/sakorman/web-scan/commit/46703c9) · [数据、截图与局限](docs/SSIM-BATCH.md)
- 参考[fused-ssim](https://github.com/rahul-goel/fused-ssim)和[Faster-GS论文](https://fhahlbohm.github.io/faster-gaussian-splatting/assets/hahlbohm2026fastergs.pdf)。本轮只做Burn张量合批，没有移植融合内核；下一步验证Wgpu融合的可行性。

### 训练阶段耗时剖面与近期研究（实验） · [46703c9](https://github.com/sakorman/web-scan/commit/46703c9)

- 研究[Faster-GS](https://fhahlbohm.github.io/faster-gaussian-splatting/assets/hahlbohm2026fastergs.pdf)、[官方代码](https://github.com/nerficg-project/faster-gaussian-splatting)和[fused-ssim](https://github.com/rahul-goel/fused-ssim)，把CUDA / MPS实现与本机Burn/Wgpu/Metal边界分开记录。
- 三场景12次训练中同步采样forward、loss、backward、update。backward随高斯数量增长最明显，TUM desk从6.541 ms增至9.656 ms；profile会改变调度，不作为速度结论，不替换正式训练器。
- 完整SH3、同相机评分、浏览器加载与拖动漫游通过；保留原始阶段记录，下一步做不改变数学结果的SSIM统计批处理微基准。[实测、截图和研究链接](docs/STAGE-PROFILE.md)

## 2026-10-23

### 三场景图像缓存与近期研究（未采用） · [7807baa](https://github.com/sakorman/web-scan/commit/7807baa)

- 关键结论：复用GPU图像张量可省掉97.2%～99.6%的重复转换，但三场景训练耗时基本不变，缓存保留23.6～189 MiB图像数据，因此不默认启用。
- ETH3D两组和TUM 96帧共完成12次同相机重建；GPU缓存正确性、完整SH3、留出评分和浏览器漫游检查通过。保留原照片和历史模型，下一步先测GPU光栅化、损失及优化器开销。[基线](https://github.com/sakorman/web-scan/commit/4670af9) · [完整实测与截图](docs/TENSOR-CACHE.md)
- 本轮参考：[Faster-GS论文](https://fhahlbohm.github.io/faster-gaussian-splatting/assets/hahlbohm2026fastergs.pdf)与[官方代码](https://github.com/nerficg-project/faster-gaussian-splatting)、[FastGS](https://github.com/fastgs/FastGS)、[EffGS预印本](https://arxiv.org/abs/2609.39553)。区分CUDA与本机Metal，未把论文倍速当作本地结果。

### 固定增点索引顺序（实验） · [4670af9](https://github.com/sakorman/web-scan/commit/4670af9)

- 在实验训练器中先排序当前高斯索引，再读取父高斯属性和分配随机偏移；回归验证不同遍历顺序下的对应关系、成员和关闭开关时的行为。候选116次增点 / 裁剪调用全部升序。
- 八次3000步同相机训练中，pipes平均PSNR / SSIM提高，office均值略降，实际选中集合和最终模型仍变化；保留实验开关，不替换正式引擎，也不宣称完整确定性。
- 完整SH3、输入与评估摘要、浏览器加载和拖动漫游检查通过。排序增加CPU工作，快照与轮询影响耗时，未建立提速结论；未重测SfM和端到端时间。下一步用已有TUM密集照片补充同相机质量对照。[基线](https://github.com/sakorman/web-scan/commit/fd349f0) · [完整记录、截图与限制](docs/REFINE-ORDER.md)

### 单独调整旋转稳定项（未采用） · [fd349f0](https://github.com/sakorman/web-scan/commit/fd349f0)

- 在实验训练器中只为旋转参数设置 `epsilon=1e-8`，其余参数保留 `1e-15`；覆盖值随优化器状态保存。实际GPU状态测试通过，四次候选的120条初始化和增点 / 裁剪记录均保留覆盖值。
- 八次同相机3000步训练中，早期旋转差异缩小，但 pipes / office 平均 PSNR 分别由19.0128 / 21.5642降至18.1541 / 20.8113，平均SSIM也下降，未采用。最终模型仍有波动，不能用参数差异缩小代替画质收益。
- 完整SH3、评估输出、浏览器加载与拖动漫游检查通过；计时包含观测开销，未声称提速，未重测SfM与端到端时间。暂停提高稳定项的尝试，下一步单独检查增点索引顺序。[基线](https://github.com/sakorman/web-scan/commit/ed2cf5c) · [数据、截图与限制](docs/ROTATION-ADAM.md)

## 2026-10-22

### Adam 稳定项与首轮梯度（未采用） · [ed2cf5c](https://github.com/sakorman/web-scan/commit/ed2cf5c)

- 记录首轮实际梯度和原始四元数，确认近零旋转梯度在 `epsilon=1e-15` 时仍产生接近 `0.001` 的更新；八次实际更新与独立公式核对的最大误差小于 `3.24e-8`。
- 将所有参数组的 epsilon 改为 `1e-8`，两场景各两次同相机对照中，早期旋转差异缩小，但最终模型仍不确定，pipes 平均 PSNR 17.5624 → 17.1881。未改默认参数，保留实验追踪与结果。
- 八次3000步训练、完整SH3、评估输出和浏览器拖动漫游检查通过。梯度读回与状态快照有同步开销，未把耗时差当作提速，未重测 SfM 与端到端时间。下一步单独验证旋转组，避免影响其它参数的收敛。[基线](https://github.com/sakorman/web-scan/commit/7004e98) · [数据、同视角图片与限制](docs/ADAM-EPSILON.md)

### 补传 GPU 随机种子（实验） · [7004e98](https://github.com/sakorman/web-scan/commit/7004e98)

- 定位到当前依赖组合中 Burn 与 CubeCL 使用不同随机状态；只设置 CLI 种子时，训练前的旋转和透明度就已不同。补传 GPU 种子，实际 Normal / Uniform 张量回归测试先复现失败，再通过。
- 四次定位实验、八次固定条件对照完成。候选两场景的初始完整 PLY 各自逐字节一致，但第一次参数更新后仍分叉，最终模型仍有变化；保留实验修正，未替换正式引擎。
- 完整 SH3、同相机评分与浏览器加载、拖动漫游检查通过。快照带来 GPU 同步和读回开销，两次短训练不足以确认画质或速度收益；未重测 SfM 与端到端时间。下一步检查首轮梯度与优化器更新。[基线](https://github.com/sakorman/web-scan/commit/83afb9f) · [实测、截图与限制](docs/GPU-SEED.md)

### 点编号读取与初始化排序（实验） · [83afb9f](https://github.com/sakorman/web-scan/commit/83afb9f)

- 发现 Brush 二进制点编号按错误字节序读取，ID `1` 会变成 `72057594037927936`。新增二进制 / 文本一致性测试先复现失败，再改为小端读取；8 项读取测试和2项初始化测试通过，坐标、颜色与轨迹载荷一致。
- 修正后按真实编号排序，两场景的点序列各自重复一致，但首次采样权重与模型仍变化。八次正式训练的画质有升有降，未替换官方引擎；修正前的八次探查另存，不混入最终统计。
- 完整 SH3、参考图与真实点编号核对通过，浏览器加载和拖动漫游正常。保留修正及实验开关，下一步区分随机初始化与首轮计算的差异；本轮未重测 SfM 或端到端速度。[基线](https://github.com/sakorman/web-scan/commit/8fca097) · [实测、截图与限制](docs/INIT-ORDER.md)

## 2026-10-21

### 固定加权采样与初始化追踪（实验） · [8fca097](https://github.com/sakorman/web-scan/commit/8fca097)

- 在独立 Brush 构建中，给高斯加权采样增加由 CLI 种子初始化、跨调用保留的 RNG；相同权重流的重复测试通过，5 项采样测试全部通过。
- 两场景各四次正式训练的照片消费序列一致，但同一初始点集合出现四种编号遍历顺序，第一次采样的权重也已不同。模型仍不确定，不能把画质波动全部归因于采样种子；下一步先固定初始化点顺序。
- 八次 3000 步训练、两次验收和浏览器漫游检查通过。平均 PSNR 未改善，保留实验补丁，不替换正式引擎；计时只覆盖训练、评估和导出，未声称提速或确定性收益。[基线](https://github.com/sakorman/web-scan/commit/1676da3) · [完整追踪、同视角图片与局限](docs/SAMPLING-SEED.md)

### 单生产任务读图实验（未采用） · [1676da3](https://github.com/sakorman/web-scan/commit/1676da3)

- 在独立 Brush 源码构建中增加实际消费照片的顺序追踪，对 10 / 1 个图像生产任务做两场景各两次对照；八次正式训练的 24,000 条记录完整。单生产任务的照片序列各场景两次一致，但模型仍不同。
- 同相机评分中，pipes / office 平均 PSNR 分别由 18.024 / 20.472 降至 17.186 / 19.747，未通过画质要求；不替换正式引擎。所有模型保留完整 SH3，浏览器加载与拖动漫游正常，仍有拉丝和细节缺失。
- 保留 Apache-2.0 实验补丁、构建信息和同视角图片。首次源码构建含依赖获取约 14 分钟，运行时未证明提速，未重测 SfM 或端到端耗时。下一步单独固定高斯加权采样，继续检查剩余波动。[基线](https://github.com/sakorman/web-scan/commit/0197599) · [完整结果与复现](docs/LOADER-ORDER.md)

## 2026-10-20

### 记录实际训练器与种子 · [0197599](https://github.com/sakorman/web-scan/commit/0197599)

- 修复自定义 Brush 也被硬编码为 `0.3.0` 的报告问题；每次记录 CLI 自报版本、可执行文件 SHA-256、读取耗时，并显式传入原有种子 42。版本无法读取时记为未知，历史报告不补造字段。
- 查到图像加载队列和加权采样存在 CLI 种子未统一约束的来源，报告明确不保证确定性。本轮未修改训练器，未把短训练波动当作优化收益。
- 四次固定相机训练、两次冷 SfM 完整任务和下载校验通过；新版标识读取约 61–70 ms。114 项后端、18 项前端测试、类型检查及构建通过，浏览器加载和拖动漫游正常。下一步分别验证训练图像顺序与增点采样。[基线](https://github.com/sakorman/web-scan/commit/e600bfe) · [实测与限制](docs/ENGINE-IDENTITY.md)

### 增强尺度约束实验（未采用） · [e600bfe](https://github.com/sakorman/web-scan/commit/e600bfe)

- 固定两组照片的相机、3000 步 / 512 像素与 15 万高斯预算，将尺度损失权重从 `1e-8` 提到 `1e-6`，各重复两次，共八次真实训练。
- office 平均 PSNR / SSIM 提高，pipes 一升一降且均值未改善；最长轴尺度也未一致缩小，因此不调整默认参数。约半秒的平均耗时差受进程轮询粒度影响，未作为提速收益。
- 八个模型的完整 SH3、评估集合与参考图校验通过，浏览器加载及拖动漫游正常；仍有拉丝和细节缺失。本轮使用缓存几何，未重新测量 SfM 和端到端耗时。下一步优先检查训练采样与增点顺序的波动。[基线](https://github.com/sakorman/web-scan/commit/2a9cdba) · [完整记录及同视角图片](docs/SCALE-LOSS.md)

### 固定 Mapper 种子实验（未采用） · [2a9cdba](https://github.com/sakorman/web-scan/commit/2a9cdba)

- 两组真实照片各三次前后对照，十二次均完整定位。候选固定 Mapper / Triangulator 种子后，相机及稀疏文件各场景三次一致。
- office SfM 均值 8.60 → 12.26 秒，增加约 42.6%，稀疏点也不同；四次短训练没有建立同相机质量收益，因此保留现有默认设置。
- 模型与评估输出完整，浏览器加载和管道房漫游正常。只记录实验，不将不同位姿的分数直接比较为算法收益。后续继续检查相机约束、覆盖与训练调度。[基线](https://github.com/sakorman/web-scan/commit/43d5895) · [代价与复现](docs/MAPPER-SEED.md)

## 2026-10-19

### 固定两视图几何验证种子 · [43d5895](https://github.com/sakorman/web-scan/commit/43d5895)

- 仅将两视图 RANSAC 的随机种子固定为 42，保留原有阈值和并行特征流程，训练报告记录 `matching_seed`。
- 两组真实照片各三次前后对照：原版每场景三种完整两视图记录，候选各一种，十二次均完整定位。office 后续相机结果仍有差异，未声称完整 SfM 或 GPU 训练确定性。
- TUM 96帧同镜头SfM、两组新重建与一次缓存复用通过；107项后端、18项前端测试、类型检查及构建通过。本轮改善重复性，不将时间差或短训练分数作为质量/速度提升。后续继续检查 Mapper 与训练调度。[基线](https://github.com/sakorman/web-scan/commit/1972308) · [测量与限制](docs/SFM-SEED.md)

### 固定位姿流程的图像顺序 · [1972308](https://github.com/sakorman/web-scan/commit/1972308)

- 查明相同照片的 SIFT 特征相同，但并行完成顺序会改变图像 ID。改为先按采集名登记照片与相机，再保留 4 线程特征提取；焦距先验和相机策略不变。
- 两场景原版/新版各三次、共十二次 SfM：原版每场景出现三种 ID 排列，新版始终一种；本轮均完整定位，没有据此宣称注册率或画质提高。几何验证与训练仍有波动。
- 两组新重建及一次缓存复用通过，107 项后端、18 项前端测试、类型检查与构建通过。新增登记有少量开销，旧模型与照片保留，旧位姿缓存按实现版本自动隔离。[基线](https://github.com/sakorman/web-scan/commit/577874d) · [证据、代价与后续问题](docs/SFM-ORDER.md)

### 连续桌面实拍示例 · [577874d](https://github.com/sakorman/web-scan/commit/577874d)

- 增加 TUM RGB-D freiburg1_desk 示例：从 613 张 RGB 中按时间顺序均匀选择 96 帧，保持 640×480，完整性校验后才可导入；不使用深度、原始标定或真值位姿。
- 示例面板按数据集显示真实作者与许可，桌面三列、手机面板内滚动。ETH3D 两组准备命令及来源保持兼容。
- TUM 同镜头模式定位 96/96，3000 步 / 512 像素任务耗时 80.89 秒，生成 72,150 个高斯；完整 SH3 与下载字节校验通过。这是扩展数据基准，不是不同场景之间的算法提速比较。
- 两组 ETH3D 也完成真实重建。pipes 自动模式首次为 11/14，同字节输入单独重算为 14/14，记录波动并保留首次成绩；后续继续检查定位稳定性。
- 105 项后端、18 项前端测试、类型检查和构建通过，浏览器导入、漫游、原照片对照及下载面板验证通过。小字、线缆和轨迹外区域仍有缺失。[采样、完整实测与截图](docs/TUM-DESK.md)

## 2026-10-16

### 提前结束增点实验（未采用） · [305eb5b](https://github.com/sakorman/web-scan/commit/305eb5b)

- 固定两组公开照片的相机、3000 步、512 像素和 15 万高斯预算，对 80% / 50% 增点结束时间各重复两次，共八次真实训练。
- 50% 的实际高斯约减半，耗时少约 1 秒，但两场景平均 PSNR / SSIM 均下降，未通过画质要求；默认保持 80%。不把不同点数的短时差当作相同质量提速。
- 完整 SH3、评估图片集合与参考摘要验证通过，补充同视角评分和浏览器漫游观察。本轮只记录实验；下一步优先检查更密集实拍覆盖、位姿与曝光一致性。[基线](https://github.com/sakorman/web-scan/commit/8d47231) · [结果与复现](docs/GROWTH-STOP.md)

### 提高网页预览精度 · [8d47231](https://github.com/sakorman/web-scan/commit/8d47231)

- 模型加载后，中间累加缓冲也使用扩展格式，减少再次量化带来的误差；已有模型刷新页面即可生效，训练参数与下载文件不变。
- 固定两组模型、相机、512 像素画幅和正式背景，六个留出视角的 PSNR / SSIM 均提高。pipes 平均 PSNR 16.235 → 16.683 dB，office 19.742 → 20.071 dB；这是网页预览评分。
- 静止相机下的中间纹理容量分别增加约 4.59 / 3.56 MiB，未验证真实手机峰值。未采用像素误差略升的滤波调整。
- 两组真实照片重建与下载、99 项后端和 18 项前端测试、类型检查及构建通过；正式页面拖动漫游正常。下一步检查更密集采集与不同分辨率的预览代价。[基线](https://github.com/sakorman/web-scan/commit/b9bff48) · [同视角对照与复现](docs/VIEWER-PRECISION.md)

## 2026-10-15

### 拒绝不完整模型输出 · [b9bff48](https://github.com/sakorman/web-scan/commit/b9bff48)

- 复现并修复空 PLY、残缺高阶颜色和非有限球谐系数被接受的问题。模型发布前检查完整颜色字段，Brush 输出要求 SH3；失败时保留原始模型与中间文件。
- 两组真实照片完成冷 SfM、1000 步 Metal 训练和下载验证；同一训练输出经新旧转换器生成的 PLY / splat 逐字节一致。99 项后端、18 项前端测试与构建通过，浏览器模型加载和拖动正常。
- 较大历史模型的新增读取校验开销约 20–30 ms，不改变训练参数。下一步继续核对采集覆盖及同视角渲染一致性。[基线提交](https://github.com/sakorman/web-scan/commit/f56c7b4) · [复现、成本与截图](docs/PLY-VALIDATION.md)

### 统一变更记录 · [f56c7b4](https://github.com/sakorman/web-scan/commit/f56c7b4)

- 新增本文件，补齐项目建立以来的功能、修复、性能改进与未采用实验；README 增加入口。
- 后续每轮记录改动原因、采用或放弃的方案、验证结果、代价与下一步，并链接详细报告及对应提交。只有状态检查且没有新结果时不追加重复条目。
- 本轮仅整理文档，检查本地链接与历史提交引用，不重复运行重建实验。

### 示例归档单次读取 · [8937a2b](https://github.com/sakorman/web-scan/commit/8937a2b)

- 解决逐张读取照片反复解压 7z 的等待，改为单次流式读取；完整处理并确认解压成功后才发布示例。错误归档或读取中断时保留旧文件。
- pipes 准备耗时 54.87 → 5.54 秒，office 183.94 → 11.25 秒；40 张 JPEG 和来源清单逐字节一致。每组各一次，未清理系统文件缓存，数字只覆盖归档准备。
- 两组新准备照片完成冷 SfM、1000 步 Metal 训练及下载校验，定位分别为 14/14 和 26/26；86 项后端、18 项前端测试及构建通过。
- 未调整训练参数；真实采集覆盖与细节收敛仍需继续改进。[测量、渲染与复现](docs/SAMPLE-PREPARATION.md)

### 保留照片焦距先验 · [d0592cc](https://github.com/sakorman/web-scan/commit/d0592cc)

- 修复导入和示例准备丢失 EXIF 等效焦距的问题。只保留有效焦距，方向校正、像素处理和去重规则不变；无效或缺失字段按无先验处理。
- 两组官方照片均为 20 mm，COLMAP 正确识别全部 40 张照片的先验。四次完整位姿求解与短训练、76 项后端测试通过。
- 短训练成绩有升有降，不能据此宣称普遍画质收益。旧采集里已丢失的焦距无法恢复，需要重新导入原照片。[验证与限制](docs/FOCAL-PRIOR.md)

## 2026-10-14

### 重复对照与增点间隔实验 · [61b2d9a](https://github.com/sakorman/web-scan/commit/61b2d9a)

- 基准脚本支持重复次数、分辨率、交错执行顺序、适配器源码快照和均值/范围汇总，拒绝缺失、重复或配置不一致的运行。
- 两场景固定相机、3000 步、512 像素、15 万高斯预算，对 100/50 步增点间隔各重复两次，共八次真实训练。另完成 500 步命令行验收，64 项后端测试通过。
- **未采用 50 步增点间隔**：点数约增加 5–7 倍，耗时约增加 39%，未同时获得稳定画质收益。默认保持 100 步，后续继续按重复实验判断参数。[完整对照](docs/REFINEMENT.md)

### 评测输出完整性 · [8c4db08](https://github.com/sakorman/web-scan/commit/8c4db08)

- 修复训练器评测失败仍被发布为成功，以及两份结果同时缺图仍可参与比较的问题。
- 发布前检查最终步数、完整评估视图、PNG 解码/尺寸与有限指标；对照时校验相机、参考照片和图像摘要。失败保留原始模型供排查。
- 57 项后端测试、两组留出模式和一组全照片模式真实训练通过，历史完整成绩复核后不变。本轮改善结果可信度，未改变训练算法。[验证记录](docs/EVALUATION.md)

## 2026-10-13

### 查看器按需绘制 · [e2ebf38](https://github.com/sakorman/web-scan/commit/e2ebf38)

- 模型静止、原照片覆盖或页面隐藏时暂停重复绘制；拖动、缩放、窗口变化及异步排序后再更新。
- 静止观察 75 秒、原照片对照 105 秒均无额外绘制；同视角模型区域截图逐像素一致。18 项前端测试、构建和两组真实照片短训练通过，浏览器检查拖动、阻尼停止、视角切换及手机布局。
- 降低闲置绘制开销，未测得训练提速或整机功耗收益。[测量与截图](docs/VIEWER-PERFORMANCE.md)

### 文档整理 · [5063bdb](https://github.com/sakorman/web-scan/commit/5063bdb)

- 精简 README、测试与性能文档，围绕功能、使用方式和真实结果说明现状；保留依赖署名、许可及实验限制。
- 仅调整文档，不将其记作重建性能或画质改进。

### 训练进度与导出清理 · [b603dba](https://github.com/sakorman/web-scan/commit/b603dba)

- 读取实际训练日志显示步数和高斯数，首次中间 PLY 写出前即可更新进度；每次最多读取 64 KiB。
- 最终模型验证并转换成功后，只清理本次任务的较早 PLY。两组基线的导出留存空间减少 81.6%/82.7%，最终模型字节不变。
- **未采用仅在最后导出的策略**：四次长训练中速度差仅 1.3%–3.6%，pipes 一次同条件对照出现画质下降。保留每 500 步导出，不据此宣称训练加速。
- 42 项后端测试及真实 API 短训练、进度显示、下载校验通过。后续需继续检查固定种子下训练输入顺序与成绩波动。[完整实验与取舍](docs/ITERATIONS.md)

### 简化视角操作 · [ef8165f](https://github.com/sakorman/web-scan/commit/ef8165f)

- 移除画面上的方向按钮和悬浮操作提示，直接拖动调整视角，保留环绕、漫游和重置入口。
- 仅调整交互，不改变重建模型。后续查看器回归覆盖拖动与漫游操作。[交互验证](docs/VIEWER-PERFORMANCE.md)

## 2026-10-12

### 位姿、完整 PLY 与照片对照 · [3d40af5](https://github.com/sakorman/web-scan/commit/3d40af5)

- 预览改为读取完整 SH3 PLY；新增按真实相机内参、位姿和画幅查看模型与原照片、历史模型和质量报告。
- 增加特征预算与 guided matching，提供同镜头模式；新增分辨率和训练档位，区分全照片最终模型与留出评测。
- pipes 定位由此前 11/14 达到 14/14，office 26/26；多个参数同时调整，未将收益归因于单个参数。固定相机的快速/精细对照中，两场景 SSIM 提高，但 office PSNR 略降且耗时增加。
- 34 项后端、12 项前端测试与构建通过，桌面/手机视角对照、漫游、历史链接和下载通过浏览器验证。采集轨迹外仍可能出现空洞与拉丝。[同条件评分及限制](docs/QUALITY.md)

### 公开照片示例 · [e14786b](https://github.com/sakorman/web-scan/commit/e14786b)

- 示例面板新增 ETH3D office 26 张、pipes 14 张实拍照片，展示来源、作者和许可；校准场景归入调试选项。
- 示例完整性与 SHA-256 校验通过后才允许导入；修复空目录被当作可用示例的问题。
- pipes 完成实际导入、训练及 PLY 下载，首次定位 11/14，模型仍粗且有缺失视角。30 项后端、8 项前端测试、构建及桌面/手机面板验证通过。[实测与截图](docs/QA.md)

### 上传并发与 SfM 复用 · [cea80ce](https://github.com/sakorman/web-scan/commit/cea80ce)

- 照片/视频帧采用最多 3 路并发上传与有界队列，按原始帧序恢复顺序；失败后保留可恢复内容。
- 相同冻结采集重复重建时，校验并复用位姿、稀疏几何及去畸变照片。缓存缺失、损坏或求解版本变化时重新计算。
- office 相同 1000 步任务首次 16.76 秒、复用 7.12 秒，SfM 阶段 8.60 → 0.034 秒；这是同一采集的缓存收益。25 项后端、8 项前端测试、构建和浏览器视频导入验证通过。[计时范围与缓存规则](docs/PERFORMANCE.md)

## 2026-10-10

### 采集详情页 · [f60b60f](https://github.com/sakorman/web-scan/commit/f60b60f)

- 采集优先展示照片计数、12 张门槛、相机与照片/视频导入，完成训练后进入模型工作台。项目链接可刷新恢复，删除只隐藏项目并保留本地文件。
- 浏览器导入 12 秒视频得到 12 张不同照片；26 张 office 照片完成采集、提交和短训练。20 项后端、5 项前端测试与构建通过；手机摄像头授权仍待实机测试。[回归记录](docs/QA.md)

### 模型下载 · [ba5a502](https://github.com/sakorman/web-scan/commit/ba5a502)

- 桌面与手机均增加可见下载入口，显示 PLY、splat、相机和报告的大小；文件名包含项目与任务编号，支持 HTTP Range。
- office PLY 11,854,178 字节、splat 1,607,136 字节的实际下载与磁盘摘要一致；17 项后端测试、前端构建及手机入口验证通过。[下载验证](docs/QA.md)

### 一屏工作台 · [2cc0dc1](https://github.com/sakorman/web-scan/commit/2cc0dc1)

- 模型、照片缩略条、参数和状态放入紧凑工作台，照片全集、日志、帮助与下载放入独立面板；项目分页，手机设置按需展开。
- 浏览器检查 1440×900、1280×720 和 390×844，无主页面溢出，并验证面板、项目切换、训练取消及重新提交。[布局与检查](docs/QA.md)

### 蓝色主题 · [1b156a5](https://github.com/sakorman/web-scan/commit/1b156a5)

- 界面配色改为蓝色，重新截取实际运行页面；桌面和 390 像素布局、前端构建通过。[界面约定](docs/UI-DESIGN.md)

### 状态修复与实测文档 · [b37257c](https://github.com/sakorman/web-scan/commit/b37257c)

- 修复恢复上传时参数丢失、漫游重置切换模式、项目列表状态滞后、过期请求覆盖当前项目，以及非有限元数据导致读取失败的问题。
- 补充真实截图与重建限制；office 26 张输入定位 25 张，3000 步任务 42.83 秒。14 项后端、4 项前端测试、构建和浏览器操作回归通过。[当轮结果](docs/QA.md)

### 初始版本 · [29d25fc](https://github.com/sakorman/web-scan/commit/29d25fc)

- 建立本地照片采集、上传恢复、项目管理、持久化任务、COLMAP 位姿求解、Brush GPU / PyTorch CPU 训练、模型预览与导出流程。
- 提供本地启动方式、依赖许可与架构文档；CUDA 后端和真实手机采集仍有待补充验证。[项目架构](docs/ARCHITECTURE.md)
