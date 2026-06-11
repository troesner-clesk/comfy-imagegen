#!/usr/bin/env python3
"""
imagegen — a dead-simple "say it and it happens" image generator for ComfyUI.

Usage:
    imagegen "an astronaut riding a horse, cinematic photo"
    imagegen "portrait of an old woman" --neg "blurry, extra fingers" --steps 35 --size 832x1216
    imagegen "logo, minimalist" --batch 4
    imagegen "a blue sports car" --from car.png --strength 0.6   # evolve an existing image

Sends an SDXL txt2img (or img2img) workflow to a running ComfyUI instance,
waits for the result, saves it to the output folder and (optionally) opens it.
No paths are hardcoded: the checkpoint is chosen live from the ComfyUI API.

Requirements: Python 3.8+, Pillow. A running ComfyUI with at least one SDXL checkpoint.
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import subprocess

HOST = os.environ.get("COMFY_HOST", "http://localhost:8188")
OUTDIR = os.environ.get("IMAGEGEN_OUT", os.path.expanduser("~/imagegen/output"))
# Preferred checkpoint (case-insensitive substring). Falls back to the first available one.
PREFER_CKPT = os.environ.get("COMFY_CKPT", "")


def api(path, data=None):
    url = f"{HOST}{path}"
    if data is not None:
        data = json.dumps(data).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def pick_checkpoint():
    info = api("/object_info/CheckpointLoaderSimple")
    names = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    if not names:
        sys.exit("No checkpoint found in ComfyUI. Put a .safetensors file in models/checkpoints.")
    if PREFER_CKPT:
        for n in names:
            if PREFER_CKPT.lower() in n.lower():
                return n
    return names[0]


def build_txt2img(ckpt, pos, neg, w, h, steps, cfg, seed, sampler, scheduler, batch):
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": w, "height": h, "batch_size": batch}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": pos, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["4", 1]}},
        "3": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
                         "model": ["4", 0], "positive": ["6", 0],
                         "negative": ["7", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "imagegen", "images": ["8", 0]}},
    }


def upload_image(path):
    """Upload a local image into ComfyUI's input folder. Returns the LoadImage reference."""
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        alt = os.path.join(OUTDIR, os.path.basename(path))
        if os.path.isfile(alt):
            path = alt
        else:
            sys.exit(f"Source image not found: {path}\n"
                     f"(Tip: pass just the filename to look it up in {OUTDIR}.)")
    with open(path, "rb") as f:
        content = f.read()
    boundary = f"----imagegen{os.getpid()}"
    fname = os.path.basename(path)
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{fname}"\r\n'.encode(),
        b"Content-Type: image/png\r\n\r\n", content, b"\r\n",
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n',
        f"--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(f"{HOST}/upload/image", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        info = json.loads(r.read().decode())
    ref = info["name"]
    if info.get("subfolder"):
        ref = f"{info['subfolder']}/{ref}"
    return ref


def build_img2img(ckpt, image_ref, pos, neg, steps, cfg, seed, sampler, scheduler, denoise):
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "10": {"class_type": "LoadImage", "inputs": {"image": image_ref}},
        "11": {"class_type": "VAEEncode", "inputs": {"pixels": ["10", 0], "vae": ["4", 2]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": pos, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["4", 1]}},
        "3": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": sampler, "scheduler": scheduler, "denoise": denoise,
                         "model": ["4", 0], "positive": ["6", 0],
                         "negative": ["7", 0], "latent_image": ["11", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "imagegen", "images": ["8", 0]}},
    }


def strip_metadata(data):
    """Return PNG bytes without text chunks (removes the embedded prompt/workflow)."""
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.load()
        clean = Image.new(img.mode, img.size)
        clean.putdata(list(img.getdata()))
        out = io.BytesIO()
        clean.save(out, format="PNG")  # no pnginfo -> metadata gone
        return out.getvalue(), True
    except Exception:
        return data, False


def main():
    ap = argparse.ArgumentParser(description="Dead-simple image generator for ComfyUI (SDXL)")
    ap.add_argument("prompt", nargs="+", help="What to draw")
    ap.add_argument("--neg", default="lowres, bad anatomy, worst quality, low quality, blurry, watermark, text",
                    help="Negative prompt")
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--cfg", type=float, default=5.5)
    ap.add_argument("--size", default="1024x1024", help="WxH, e.g. 832x1216")
    ap.add_argument("--seed", type=int, default=-1, help="-1 = random")
    ap.add_argument("--sampler", default="dpmpp_2m")
    ap.add_argument("--scheduler", default="karras")
    ap.add_argument("--batch", type=int, default=1, help="Number of images")
    ap.add_argument("--from", dest="src", metavar="IMAGE.png",
                    help="Evolve an existing image (path or just a filename from the output folder)")
    ap.add_argument("--strength", type=float, default=0.55,
                    help="With --from: how much to change. 0.3=subtle retouch, 0.55=noticeable, 0.85=strong reinterpretation")
    ap.add_argument("--open", action="store_true", help="Open the image after creating it (default: just save)")
    ap.add_argument("--keep-meta", action="store_true",
                    help="Keep prompt metadata in the PNG (default: strip it)")
    args = ap.parse_args()

    pos = " ".join(args.prompt)
    try:
        w, h = (int(x) for x in args.size.lower().split("x"))
    except ValueError:
        sys.exit(f"--size must be WxH, e.g. 1024x1024 (got: {args.size})")
    seed = args.seed if args.seed >= 0 else random.randint(0, 2**32 - 1)

    try:
        api("/system_stats")
    except urllib.error.URLError:
        sys.exit(f"ComfyUI is not reachable at {HOST}.\nStart ComfyUI and try again.")

    ckpt = pick_checkpoint()
    os.makedirs(OUTDIR, exist_ok=True)
    client_id = f"imagegen-{os.getpid()}-{seed}"

    if args.src:
        image_ref = upload_image(args.src)
        wf = build_img2img(ckpt, image_ref, pos, args.neg, args.steps, args.cfg,
                           seed, args.sampler, args.scheduler, args.strength)
        print(f"~  evolve from \"{os.path.basename(args.src)}\"  ->  \"{pos}\"")
        print(f"   checkpoint={ckpt}  strength={args.strength}  steps={args.steps}  cfg={args.cfg}  seed={seed}")
    else:
        wf = build_txt2img(ckpt, pos, args.neg, w, h, args.steps, args.cfg,
                           seed, args.sampler, args.scheduler, args.batch)
        print(f"*  \"{pos}\"")
        print(f"   checkpoint={ckpt}  {w}x{h}  steps={args.steps}  cfg={args.cfg}  seed={seed}  batch={args.batch}")

    pid = api("/prompt", {"prompt": wf, "client_id": client_id})["prompt_id"]

    print("   rendering", end="", flush=True)
    images = []
    while True:
        time.sleep(1.0)
        print(".", end="", flush=True)
        try:
            hist = api(f"/history/{pid}")
        except urllib.error.URLError:
            continue
        if pid not in hist:
            continue
        entry = hist[pid]
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            print()
            msgs = status.get("messages", [])
            sys.exit(f"ComfyUI error while rendering:\n{json.dumps(msgs, indent=2)}")
        for node in entry.get("outputs", {}).values():
            for im in node.get("images", []):
                images.append(im)
        if images:
            break
    print(" done.")

    saved = []
    for im in images:
        q = urllib.parse.urlencode({"filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                    "type": im.get("type", "output")})
        with urllib.request.urlopen(f"{HOST}/view?{q}", timeout=60) as r:
            data = r.read()
        if not args.keep_meta:
            data, ok = strip_metadata(data)
            if not ok:
                print("   ! could not strip metadata (Pillow missing?) — image still contains the prompt")
        dest = os.path.join(OUTDIR, im["filename"])
        with open(dest, "wb") as f:
            f.write(data)
        saved.append(dest)
        print(f"   saved {dest}")

    if saved and args.open:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([opener, saved[0]])
    elif saved:
        print(f"   (saved, not opened — view with:  open '{saved[0]}' )")


if __name__ == "__main__":
    main()
