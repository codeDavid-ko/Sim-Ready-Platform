"""로컬 Stable Diffusion 텍스처 생성 워커 (NdotLight 텍스처 카드용, 무료·로컬).

WSL 의 전용 venv(~/sd_texture_venv, torch cu128 + diffusers)에서 실행된다.
프롬프트 → 타일러블 albedo(SD-Turbo) 생성 → height 기반 normal + roughness 맵 파생
→ <out>/albedo.png, normal.png, roughness.png + meta.json + status.txt.

usage:
  python sd_gen_texture.py --prompt "rusted steel plate, pbr, seamless" \
      --out /mnt/c/.../run --size 768 --steps 4 --seed 0
NVIDIA/외부 API 키 불필요 — 가중치는 HuggingFace 캐시에서 로컬 추론.
"""

import argparse
import json
import os
import sys
import traceback

MODEL = os.environ.get("SD_TEXTURE_MODEL", "stabilityai/sd-turbo")


def _seamless(pipe):
    """Conv2d padding_mode 를 circular 로 바꿔 타일러블(이음매 없는) 텍스처 생성."""
    import torch

    for m in list(pipe.unet.modules()) + list(pipe.vae.modules()):
        if isinstance(m, torch.nn.Conv2d):
            m.padding_mode = "circular"


def _derive_maps(albedo, out_dir, strength=2.0):
    """albedo 휘도를 의사 height 로 보고 normal/roughness 맵 파생(PIL+numpy)."""
    import numpy as np
    from PIL import Image

    rgb = np.asarray(albedo.convert("RGB"), dtype=np.float32) / 255.0
    gray = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2])

    # normal: 그래디언트 → 탄젠트공간 노멀
    gy, gx = np.gradient(gray)
    nx, ny, nz = -gx * strength, -gy * strength, np.ones_like(gray)
    ln = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-8
    nrm = np.stack([nx / ln, ny / ln, nz / ln], axis=-1)
    normal_img = ((nrm * 0.5 + 0.5) * 255.0).clip(0, 255).astype(np.uint8)
    Image.fromarray(normal_img, "RGB").save(os.path.join(out_dir, "normal.png"))

    # roughness: 국소 대비가 큰 곳일수록 거칠게(간단 휴리스틱). 0.35~0.95 범위.
    detail = np.abs(gx) + np.abs(gy)
    detail = detail / (detail.max() + 1e-8)
    rough = (0.45 + 0.5 * detail)
    rough_img = (rough.clip(0, 1) * 255.0).astype(np.uint8)
    Image.fromarray(rough_img, "L").save(os.path.join(out_dir, "roughness.png"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="")
    ap.add_argument("--prompt-file", default="", help="프롬프트를 파일에서 읽음(셸 쿼팅/유니코드 회피)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", type=int, default=768)
    ap.add_argument("--steps", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    status = os.path.join(args.out, "status.txt")
    if args.prompt_file and os.path.exists(args.prompt_file):
        with open(args.prompt_file, encoding="utf-8") as f:
            args.prompt = f.read().strip()
    if not args.prompt:
        with open(status, "w") as f:
            f.write("FAILED: empty prompt")
        return 1

    try:
        import torch
        from diffusers import AutoPipelineForText2Image

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32
        pipe = AutoPipelineForText2Image.from_pretrained(MODEL, torch_dtype=dtype)
        pipe = pipe.to(device)
        try:
            _seamless(pipe)
        except Exception:  # noqa: BLE001 -- 타일러블은 best-effort
            pass

        prompt = (
            f"{args.prompt}, seamless tileable PBR material texture, top-down flat, "
            "even lighting, high detail, no shadows, no perspective"
        )
        gen = torch.Generator(device=device).manual_seed(int(args.seed))
        # SD-Turbo: guidance_scale=0.0, 1~4 step
        image = pipe(
            prompt=prompt,
            num_inference_steps=max(1, args.steps),
            guidance_scale=0.0,
            height=args.size,
            width=args.size,
            generator=gen,
        ).images[0]

        image.save(os.path.join(args.out, "albedo.png"))
        _derive_maps(image, args.out)
        json.dump(
            {"model": MODEL, "prompt": prompt, "size": args.size, "device": device},
            open(os.path.join(args.out, "meta.json"), "w"),
            ensure_ascii=False,
        )
        with open(status, "w") as f:
            f.write("DONE")
        return 0
    except Exception as e:  # noqa: BLE001
        with open(status, "w") as f:
            f.write("FAILED: " + str(e))
        with open(os.path.join(args.out, "error.log"), "w") as f:
            f.write(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
