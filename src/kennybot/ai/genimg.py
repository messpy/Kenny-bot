from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import torch
from diffusers import StableDiffusionPipeline


@dataclass
class SDConfig:
    model_id: str = "runwayml/stable-diffusion-v1-5"
    steps: int = 15
    width: int = 256
    height: int = 256
    guidance_scale: float = 7.5
    negative_prompt: Optional[str] = None
    seed: Optional[int] = None
    out: str = "output.png"


def build_pipeline(model_id: str, *, device: str = "cpu") -> StableDiffusionPipeline:
    """
    Build StableDiffusionPipeline.
    - safe to call from import side
    """
    pipe = StableDiffusionPipeline.from_pretrained(
        model_id,
        torch_dtype=torch.float32,
        safety_checker=None,
        requires_safety_checker=False,
    )
    return pipe.to(device)


def generate(
    prompt: str,
    cfg: SDConfig,
    *,
    device: str = "cpu",
    pipe: Optional[StableDiffusionPipeline] = None,
) -> Path:
    """
    Generate an image and return output path.

    Usage from import side:
        from main import SDConfig, generate
        cfg = SDConfig(steps=12, width=256, height=256, out="cat.png")
        generate("cat photo", cfg)
    """
    if pipe is None:
        pipe = build_pipeline(cfg.model_id, device=device)

    gen = None
    if cfg.seed is not None:
        gen = torch.Generator(device="cpu").manual_seed(int(cfg.seed))

    result = pipe(
        prompt=prompt,
        negative_prompt=cfg.negative_prompt,
        num_inference_steps=int(cfg.steps),
        width=int(cfg.width),
        height=int(cfg.height),
        guidance_scale=float(cfg.guidance_scale),
        generator=gen,
    )

    out_path = Path(cfg.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.images[0].save(out_path)
    return out_path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Stable Diffusion (CPU) generator (uv project)")
    p.add_argument("prompt", help="positive prompt (required)")
    p.add_argument("--model", default="runwayml/stable-diffusion-v1-5", help="HF model id")
    p.add_argument("--steps", type=int, default=15)
    p.add_argument("--w", "--width", dest="width", type=int, default=256)
    p.add_argument("--h", "--height", dest="height", type=int, default=256)
    p.add_argument("--scale", dest="guidance_scale", type=float, default=7.5)
    p.add_argument("--neg", dest="negative_prompt", default=None, help="negative prompt")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("-o", "--out", default="output.png")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    cfg = SDConfig(
        model_id=args.model,
        steps=args.steps,
        width=args.width,
        height=args.height,
        guidance_scale=args.guidance_scale,
        negative_prompt=args.negative_prompt,
        seed=args.seed,
        out=args.out,
    )
    out_path = generate(args.prompt, cfg)
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
