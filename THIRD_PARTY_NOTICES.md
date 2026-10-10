# Third-party components and references

Web Scan implements its own application, upload queue, local job orchestration, dense CPU Gaussian reference renderer, training loop and asset exporter. It uses the following independent projects. Their names, authorship and licenses remain with their respective authors.

| Component | Use | License / source |
| --- | --- | --- |
| COLMAP / PyCOLMAP | Feature extraction, matching and camera reconstruction | BSD-3-Clause, https://github.com/colmap/colmap |
| PyTorch | Tensor operations, automatic differentiation, Adam | BSD-style, https://github.com/pytorch/pytorch |
| Brush 0.3.0 (optional binary) | Native GPU training, densification and SH3 export | Apache-2.0, https://github.com/ArthurBrussee/brush |
| gsplat (optional) | CUDA differentiable Gaussian rasterization | Apache-2.0, https://github.com/nerfstudio-project/gsplat |
| gsplat.js (`gsplat` npm package) | Browser Gaussian viewer | MIT, https://github.com/huggingface/gsplat.js |
| Lucide | Interface icons | ISC / Feather MIT portions, https://github.com/lucide-icons/lucide |
| FastAPI / Starlette / Uvicorn | Local HTTP API | MIT / BSD-3-Clause, see installed distributions |
| NumPy / Pillow | Geometry arrays and images | BSD-3-Clause / HPND, see installed distributions |
| Vite / TypeScript | Frontend build and type checking | MIT / Apache-2.0 |

The CPU renderer is an original compact implementation of the published Gaussian splatting method (perspective covariance projection, depth ordering, alpha compositing and gradient optimization). It does not copy the original research repository's CUDA code. It intentionally omits production optimizations, densification and high-order spherical harmonics.

Algorithm reference: Kerbl et al., *3D Gaussian Splatting for Real-Time Radiance Field Rendering*, SIGGRAPH 2023, https://repo-sam.inria.fr/fungraph/3d-gaussian-splatting/ .

API references used: https://colmap.github.io/pycolmap/pycolmap.html and https://docs.gsplat.studio/main/apis/rasterization.html .

Browser bundle license texts are preserved under `licenses/` and copied into `dist/licenses/` at build time. Python packages retain the license files delivered by their distributions. The project's MIT license applies to original project code, not as a replacement for dependency licenses.

## Test dataset

ETH3D office: https://www.eth3d.net/datasets . The 26 real indoor images are downloaded separately from the official dataset archive. Dataset images and generated models are excluded from Git and are not licensed under the project MIT license. The local UI and training reports retain dataset attribution.
