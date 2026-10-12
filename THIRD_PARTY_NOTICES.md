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

## Test dataset

ETH3D office (26 photos) and pipes (14 photos): https://www.eth3d.net/datasets . The photos are downloaded separately from official dataset archives. Attribution: Thomas Schöps, Johannes L. Schönberger, Silvano Galliani, Torsten Sattler, Konrad Schindler, Marc Pollefeys, and Andreas Geiger, *A Multi-View Stereo Benchmark with High-Resolution Images and Multi-Camera Videos*, CVPR 2017.

ETH3D licenses its data under Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0): https://creativecommons.org/licenses/by-nc-sa/4.0/ ; dataset license statement: https://www.eth3d.net/ . The preparation script applies EXIF orientation, resizes to a 1600-pixel long edge and re-encodes JPEGs. The app estimates new camera poses and derives Gaussian models from these photos.

Dataset photos and generated models are excluded from Git and are not covered by the project MIT license. Screenshots in `docs/screenshots/` that contain ETH3D photographs or reconstructed scenes retain this attribution and the CC BY-NC-SA 4.0 terms for that content. The sample picker, local source manifests and training reports retain dataset attribution and license links. Original project code remains MIT-licensed.
