#![recursion_limit = "256"]

// Offline scorer for Brush's converted VGG LPIPS weights.
// Copy into crates/lpips/examples/lpips_score.rs in the documented Brush checkout.
use burn::{
    backend::{Wgpu, wgpu::WgpuDevice},
    tensor::{Tensor, TensorData},
};
use std::{collections::HashSet, error::Error, io, path::Path};

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}

fn tensor(image: image::RgbImage, device: &WgpuDevice) -> Tensor<Wgpu, 4> {
    let (w, h) = image.dimensions();
    let pixels: Vec<f32> = image
        .into_raw()
        .into_iter()
        .map(|x| x as f32 / 255.0)
        .collect();
    Tensor::from_data(
        TensorData::new(pixels, [1, h as usize, w as usize, 3]),
        device,
    )
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() == 1 && args[0] == "--version" {
        let weights = option_env!("WEBSCAN_LPIPS_WEIGHTS_SHA256").unwrap_or("unknown");
        if weights != "unknown"
            && (weights.len() != 64 || !weights.bytes().all(|b| b.is_ascii_hexdigit()))
        {
            return Err(invalid("Invalid compiled weight hash").into());
        }
        println!(
            "{{\"protocol\":1,\"metric\":\"lpips_vgg_brush\",\"version\":\"1\",\"weights_sha256\":\"{weights}\",\"input\":\"RGB0..1 NHWC\"}}"
        );
        return Ok(());
    }
    if args.len() != 1 {
        return Err(invalid("Usage: lpips_score DIRECTORY or --version").into());
    }
    let directory = Path::new(&args[0]);
    let manifest = std::fs::read_to_string(directory.join("pairs.tsv"))?;
    let mut pairs = Vec::new();
    let mut ids = HashSet::new();
    for line in manifest.lines() {
        let fields: Vec<_> = line.split('\t').collect();
        if fields.len() != 3 {
            return Err(invalid("Expected id, render and reference separated by tabs").into());
        }
        let id: usize = fields[0].parse()?;
        if !ids.insert(id) {
            return Err(invalid("Duplicate pair id").into());
        }
        for name in &fields[1..] {
            if name.is_empty() || Path::new(name).file_name().and_then(|v| v.to_str()) != Some(name)
            {
                return Err(invalid("Pair paths must be simple filenames").into());
            }
        }
        pairs.push((id, fields[1], fields[2]));
    }
    if pairs.is_empty() {
        return Err(invalid("No image pairs").into());
    }
    let device = WgpuDevice::default();
    let model = lpips::load_vgg_lpips::<Wgpu>(&device);
    for (id, render, reference) in pairs {
        let render = image::open(directory.join(render))?.to_rgb8();
        let reference = image::open(directory.join(reference))?.to_rgb8();
        if render.dimensions() != reference.dimensions() || render.width().min(render.height()) < 16
        {
            return Err(invalid("Images must match and have dimensions >=16").into());
        }
        let score = model
            .lpips(tensor(render, &device), tensor(reference, &device))
            .into_scalar();
        if !score.is_finite() || score < 0.0 {
            return Err(invalid("Non-finite or negative LPIPS score").into());
        }
        println!("{id}\t{score}");
    }
    Ok(())
}
