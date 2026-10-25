# Third-party components and references

Web Scan implements its own application, upload queue, local job orchestration, dense CPU Gaussian reference renderer, training loop and asset exporter. It uses the following independent projects. Their names, authorship and licenses remain with their respective authors.

| Component | Use | License / source |
| --- | --- | --- |
| COLMAP / PyCOLMAP | Feature extraction, matching and camera reconstruction | BSD-3-Clause, https://github.com/colmap/colmap |
| PyTorch | Tensor operations, automatic differentiation, Adam | BSD-style, https://github.com/pytorch/pytorch |
| Brush 0.3.0 (optional binary) | Native GPU training, densification and SH3 export | Apache-2.0, https://github.com/ArthurBrussee/brush |
| gsplat (optional) | CUDA differentiable Gaussian rasterization | Apache-2.0, https://github.com/nerfstudio-project/gsplat |
| Spark 2.3.1 | Full SH3 PLY browser rendering | MIT, https://github.com/sparkjsdev/spark |
| Three.js 0.180.0 | WebGL scene, cameras and navigation | MIT, https://github.com/mrdoob/three.js |
| fflate | Spark archive decoding dependency | MIT, https://github.com/101arrowz/fflate |
| Lucide | Interface icons | ISC / Feather MIT portions, https://github.com/lucide-icons/lucide |
| FastAPI / Starlette / Uvicorn | Local HTTP API | MIT / BSD-3-Clause, see installed distributions |
| NumPy / Pillow | Geometry arrays and images | BSD-3-Clause / HPND, see installed distributions |
| Vite / TypeScript | Frontend build and type checking | MIT / Apache-2.0 |

The CPU renderer is an original compact implementation of the published Gaussian splatting method (perspective covariance projection, depth ordering, alpha compositing and gradient optimization). It does not copy the original research repository's CUDA code. It intentionally omits production optimizations, densification and high-order spherical harmonics.

Algorithm reference: Kerbl et al., *3D Gaussian Splatting for Real-Time Radiance Field Rendering*, SIGGRAPH 2023, https://repo-sam.inria.fr/fungraph/3d-gaussian-splatting/ .

API references used: https://colmap.github.io/pycolmap/pycolmap.html and https://docs.gsplat.studio/main/apis/rasterization.html .

Browser bundle license texts are preserved under `licenses/` and copied into `dist/licenses/` at build time. Python packages retain the license files delivered by their distributions. The project's MIT license applies to original project code, not as a replacement for dependency licenses.

## Brush loading experiment

`docs/benchmarks/loader-order.patch` is an experimental modification of Brush v0.3.0, commit `3edecbb2fe79d3e2c87eeab85b15e0b1dd10d486`, by Arthur Brussee and the Brush contributors: https://github.com/ArthurBrussee/brush . It adds a producer-count switch, a consumed-image trace and an experimental CLI version label. The patch retains Apache-2.0 licensing; see [the preserved license](licenses/brush-Apache-2.0.txt). It is not enabled in the default application or included in the official Brush binary installed by this project.

`docs/benchmarks/sampling-seed.patch` is a cumulative experimental patch on the same upstream revision. It includes the loader experiment and adds persistent seeded weighted sampling, sampler tests, and initialization/refinement traces. It also remains Apache-2.0-licensed and is not enabled in the default application.

`docs/benchmarks/init-order.patch` extends these experiments with little-endian binary point-ID decoding, optional canonical point ordering, and regression tests for IDs, payload pairing and subsampling. This cumulative patch also remains Apache-2.0-licensed and does not replace the installed official engine.

`docs/benchmarks/gpu-seed.patch` adds explicit CubeCL GPU RNG seeding, a real Normal/Uniform tensor regression test, and optional early-state exports to the same cumulative experiment. It remains Apache-2.0-licensed; it does not replace the installed official engine or guarantee deterministic full training.

`docs/benchmarks/adam-epsilon.patch` adds an optional global Adam epsilon comparison and first-update gradient/raw-quaternion traces. This cumulative experiment retains Apache-2.0 licensing and is not enabled in the default application.

`docs/benchmarks/rotation-adam.patch` extends the experiment with a parameter-state epsilon override, an optional rotation-only setting, and a GPU test for persistence through updates, record conversion and device transfer. It retains Apache-2.0 licensing and does not replace the installed official engine.

`docs/benchmarks/refine-order.patch` adds optional canonical refinement-index ordering and a test of stable parent-to-sample assignment. This cumulative patch retains Apache-2.0 licensing and remains an independent experiment, not a replacement for the installed official engine.

`docs/benchmarks/tensor-cache.patch` adds optional bounded reuse of immutable training-image GPU tensors, cache-consumption traces and a GPU correctness/budget test. It retains Apache-2.0 licensing and is not enabled in the default application. Faster-GS, FastGS and EffGS are cited as research references; their implementation code is not included in this patch.

`docs/benchmarks/stage-profile.patch` adds optional synchronized stage timing for the Brush experiment. It retains Apache-2.0 licensing, changes no default application behavior, and does not include Faster-GS or fused-ssim source code.

`docs/benchmarks/ssim-batch.patch` adds optional batching of SSIM moments using existing Burn convolutions, numerical/gradient regression checks and a component benchmark. It retains Apache-2.0 licensing. The original execution path remains available; fused-ssim and Faster-GS are references only, with no source code copied from those projects.

`docs/benchmarks/ssim-reference.patch` adds an optional bounded cache of frozen reference-image SSIM means/variances, image identity propagation, fallback/numerical tests and a component benchmark. It retains Apache-2.0 licensing and remains an experimental build; cited SSIM research and external fused kernels are not copied into the patch.

`docs/benchmarks/ssim-tail.patch` records an experimental Brush build that includes the SSIM diagnostics and the input-layout regression. `docs/benchmarks/cubecl-contiguous-tail.patch` is a separate two-line change against CubeCL commit `43580a8c294f3a8140921c26f6378a12aef82881`; CubeCL is MIT OR Apache-2.0. Neither patch changes the official Brush binary or the default application.

`docs/benchmarks/ssim-tail-brush-deps.patch` and `docs/benchmarks/ssim-tail-cubecl-deps.patch` only describe the local Cargo overlays used to compile that experiment. They do not copy dependency source into this repository and retain the upstream Brush Apache-2.0 and CubeCL MIT OR Apache-2.0 terms.

## Test dataset

ETH3D office (26 photos) and pipes (14 photos): https://www.eth3d.net/datasets . The photos are downloaded separately from official dataset archives. Attribution: Thomas Schöps, Johannes L. Schönberger, Silvano Galliani, Torsten Sattler, Konrad Schindler, Marc Pollefeys, and Andreas Geiger, *A Multi-View Stereo Benchmark with High-Resolution Images and Multi-Camera Videos*, CVPR 2017.

ETH3D licenses its data under Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0): https://creativecommons.org/licenses/by-nc-sa/4.0/ ; dataset license statement: https://www.eth3d.net/ . The preparation script applies EXIF orientation, resizes to a 1600-pixel long edge and re-encodes JPEGs. The app estimates new camera poses and derives Gaussian models from these photos.

Dataset photos and generated models are excluded from Git and are not covered by the project MIT license. Screenshots in `docs/screenshots/` that contain ETH3D photographs or reconstructed scenes retain this attribution and the CC BY-NC-SA 4.0 terms for that content. The sample picker, local source manifests and training reports retain dataset attribution and license links. Original project code remains MIT-licensed.


## TUM RGB-D desk sample

TUM RGB-D `freiburg1_desk`: https://cvg.cit.tum.de/data/datasets/rgbd-dataset . Attribution: Jürgen Sturm, Nikolas Engelhard, Felix Endres, Wolfram Burgard, and Daniel Cremers, *A Benchmark for the Evaluation of RGB-D SLAM Systems*, IROS 2012.

The official dataset page currently licenses benchmark data under CC BY 4.0 unless stated otherwise: https://cvg.cit.tum.de/data/datasets/rgbd-dataset#license ; license: https://creativecommons.org/licenses/by/4.0/ . This is distinct from the ETH3D dataset's noncommercial/share-alike terms.

The preparation script selects 96 evenly spaced entries from timestamp-sorted RGB frames, keeps the original 640×480 dimensions, and converts PNG to JPEG quality 95. Depth images, supplied calibration, accelerometer data and ground-truth poses are not used. The application estimates camera poses from RGB photos. Original data and generated models remain local and are excluded from Git. TUM-derived preview images retain this attribution and CC BY 4.0 terms; the project MIT license does not replace the dataset license.
