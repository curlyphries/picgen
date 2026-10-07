#!/usr/bin/env python3
"""picgen studio: one web page and one HTTP API in front of the image models on a local GPU.

Left: chat with a local Ollama model to work out what you want.
Right: pick an image model, generate, browse the gallery, keep recurring characters.
Standard library only (no pip), so it survives OS upgrades. Image splitting and
format conversion run as short subprocesses in ComfyUI's venv, which has Pillow.

What it manages for you:
  * starts ComfyUI (headless, 127.0.0.1:8188) on the first job if it is not
    running, after unloading every resident Ollama model so the image model fits
    beside other GPU tenants; chat does the reverse (stops idle ComfyUI)
  * one job at a time; the page polls for progress; the queue survives restarts
  * stops ComfyUI after IDLE_MINUTES without a job
  * keeps every result + its prompt in ./output and shows a gallery

Config (env), all optional; see docs/07-operations.md for the full table:
  PICGEN_HOST (127.0.0.1), PICGEN_PORT (8070), COMFY_DIR (~/ComfyUI),
  COMFY_URL (http://127.0.0.1:8188), IDLE_MINUTES (5), RESERVE_VRAM (3),
  OLLAMA_URL (http://127.0.0.1:11434), CHAT_MODEL, CHAT_KEEP (10m),
  DEFAULT_IMAGE_MODEL (z-image-turbo), MAX_CHARACTERS (1), MAX_GARMENTS (2), MAX_REFS (4),
  CRITIC_AUTO (1), CRITIC_MODEL, LEARNER_MODEL, OUTPUT_DIR (./output),
  CHAR_DIR (./characters), LIBRARY_DIR (./library)
"""

import json
import os
import fcntl
from contextlib import contextmanager
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import feedback as fb               # grading, diagnosis, repair, lessons, critic (feedback.py beside this file)

HERE = Path(__file__).resolve().parent
HOST = os.environ.get("PICGEN_HOST", "127.0.0.1")
PORT = int(os.environ.get("PICGEN_PORT", "8070"))
COMFY_DIR = Path(os.environ.get("COMFY_DIR") or Path.home() / "ComfyUI")
COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
IDLE_MINUTES = int(os.environ.get("IDLE_MINUTES", "5"))
RESERVE_VRAM = os.environ.get("RESERVE_VRAM", "3")   # GB kept free for the desktop/browser while drawing
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "qwen2.5:7b")
CHAT_KEEP = os.environ.get("CHAT_KEEP", "10m")
MAX_CHARACTERS = int(os.environ.get("MAX_CHARACTERS", "1"))   # Kontext with two stitched references duplicated one and lost the other (2026-10-03 test)
OUT = Path(os.environ.get("OUTPUT_DIR") or HERE / "output")
OUT.mkdir(parents=True, exist_ok=True)
CHARS = Path(os.environ.get("CHAR_DIR") or HERE / "characters")
CHARS.mkdir(parents=True, exist_ok=True)
M = COMFY_DIR / "models"

STYLES = {
    "none": "",
    "photo": "Photographed on a full-frame camera with a 50mm lens in natural light, shallow depth of field, true-to-life colour and fine texture.",
    "illustration": "A warm editorial illustration with flat colours and a soft paper texture, clean composition.",
    "cinematic": "A cinematic 3D render with soft volumetric light, shallow depth of field and rich colour grading.",
    "cartoon": "A simple flat cartoon with thick black outlines on a plain background, the character centred.",
}
SIZES = {"square": (1024, 1024), "landscape": (1216, 832), "portrait": (832, 1216), "wide": (1344, 768)}


# ------------------------------------------------------------ image models
def _exists(*rel: str) -> bool:
    return all((M / r).exists() for r in rel)


def wf_flux(prompt, w, h, steps, seed, guidance, prefix):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "flux1-dev-fp8.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "FluxGuidance", "inputs": {"guidance": guidance, "conditioning": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
        "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler",
                                                   "scheduler": "simple", "denoise": 1.0, "model": ["1", 0],
                                                   "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0]}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["7", 0]}},
    }


def wf_zimage(prompt, w, h, steps, seed, guidance, prefix, loras=()):
    """Z-Image Turbo text to image. `loras` = [(file name in models/loras, strength)]: character or style
    LoRAs trained with ai-toolkit on Z-Image De-Turbo, chained before the sampling shift; the prompt must
    carry each LoRA's trigger word."""
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "z_image_turbo_bf16.safetensors", "weight_dtype": "default"}},
    }
    model = ["1", 0]
    for i, (name, strength) in enumerate(loras):
        wf[f"1l{i}"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": model, "lora_name": name, "strength_model": strength}}
        model = [f"1l{i}", 0]
    wf.update({
        "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {"shift": 3.0, "model": model}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b_fp8_mixed.safetensors", "type": "lumina2", "device": "default"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["3", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["3", 0]}},
        "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler",
                                                   "scheduler": "simple", "denoise": 1.0, "model": ["2", 0],
                                                   "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0]}},
        "8": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["8", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["9", 0]}},
    })
    return wf


def _kontext_head(prompt, guidance, refs):
    """Shared nodes: loaders, 1-2 reference images stitched side by side, encoded
    and attached to the conditioning (ReferenceLatent)."""
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev-kontext_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "clip_l.safetensors", "clip_name2": "t5xxl_fp8_e4m3fn_scaled.safetensors", "type": "flux", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "LoadImage", "inputs": {"image": refs[0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "10": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["7", 0]}},
    }
    img = ["4", 0]
    if len(refs) > 1:
        wf["4b"] = {"class_type": "LoadImage", "inputs": {"image": refs[1]}}
        wf["4c"] = {"class_type": "ImageStitch", "inputs": {"image1": ["4", 0], "image2": ["4b", 0], "direction": "right",
                                                            "match_image_size": True, "spacing_width": 16, "spacing_color": "white"}}
        img = ["4c", 0]
    wf["5"] = {"class_type": "FluxKontextImageScale", "inputs": {"image": img}}
    wf["6"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["3", 0]}}
    wf["8"] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["7", 0], "latent": ["6", 0]}}
    wf["9"] = {"class_type": "FluxGuidance", "inputs": {"guidance": guidance, "conditioning": ["8", 0]}}
    return wf


def wf_kontext_guide(prompt, w, h, steps, seed, guidance, prefix, refs=()):
    """Identity reference + guide reference as two SEPARATE reference latents
    (chained ReferenceLatent nodes). A stitched pair made Kontext copy the
    split-screen layout into the output (tested 2026-10-03)."""
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev-kontext_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "clip_l.safetensors", "clip_name2": "t5xxl_fp8_e4m3fn_scaled.safetensors", "type": "flux", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "10": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["7", 0]}},
    }
    cond = ["7", 0]
    for i, name in enumerate(list(refs)[:4]):
        wf[f"4{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        wf[f"5{i}"] = {"class_type": "FluxKontextImageScale", "inputs": {"image": [f"4{i}", 0]}}
        wf[f"6{i}"] = {"class_type": "VAEEncode", "inputs": {"pixels": [f"5{i}", 0], "vae": ["3", 0]}}
        wf[f"8{i}"] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": cond, "latent": [f"6{i}", 0]}}
        cond = [f"8{i}", 0]
    wf["9"] = {"class_type": "FluxGuidance", "inputs": {"guidance": guidance, "conditioning": cond}}
    wf["11"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}}
    wf["12"] = {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler",
                                                     "scheduler": "simple", "denoise": 1.0, "model": ["1", 0],
                                                     "positive": ["9", 0], "negative": ["10", 0], "latent_image": ["11", 0]}}
    wf["13"] = {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}}
    wf["14"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["13", 0]}}
    return wf


def wf_kontext(prompt, w, h, steps, seed, guidance, prefix, refs=()):
    """Character mode: the saved reference(s) condition the picture, the new
    picture is drawn from an empty latent of the requested size. The ORIGINAL
    reference is used every time (never the last output), so there is no drift."""
    wf = _kontext_head(prompt, guidance, list(refs))
    wf["11"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}}
    wf["12"] = {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler",
                                                     "scheduler": "simple", "denoise": 1.0, "model": ["1", 0],
                                                     "positive": ["9", 0], "negative": ["10", 0], "latent_image": ["11", 0]}}
    wf["13"] = {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}}
    wf["14"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["13", 0]}}
    return wf


def wf_kontext_edit(prompt, w, h, steps, seed, guidance, prefix, refs=()):
    """Edit mode: the source picture is both the reference and the starting
    latent, so composition stays and only what the prompt names changes. Further
    refs (a pose or expression view) are chained in as extra reference latents."""
    refs = list(refs)
    wf = _kontext_head(prompt, guidance, refs[:1])
    cond = ["8", 0]
    for i, name in enumerate(refs[1:3], start=1):
        wf[f"4{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        wf[f"5{i}"] = {"class_type": "FluxKontextImageScale", "inputs": {"image": [f"4{i}", 0]}}
        wf[f"6{i}"] = {"class_type": "VAEEncode", "inputs": {"pixels": [f"5{i}", 0], "vae": ["3", 0]}}
        wf[f"8{i}"] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": cond, "latent": [f"6{i}", 0]}}
        cond = [f"8{i}", 0]
    wf["9"] = {"class_type": "FluxGuidance", "inputs": {"guidance": guidance, "conditioning": cond}}
    wf["12"] = {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler",
                                                     "scheduler": "simple", "denoise": 1.0, "model": ["1", 0],
                                                     "positive": ["9", 0], "negative": ["10", 0], "latent_image": ["6", 0]}}
    wf["13"] = {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}}
    wf["14"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["13", 0]}}
    return wf


def wf_sd15(prompt, w, h, steps, seed, guidance, prefix):
    w, h = (min(w, 768) // 64) * 64, (min(h, 768) // 64) * 64
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "toonyou_beta6.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt + ", best quality, detailed", "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "lowres, bad anatomy, bad hands, text, watermark, blurry, deformed", "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": guidance, "sampler_name": "dpmpp_2m",
                                                   "scheduler": "karras", "denoise": 1.0, "model": ["1", 0],
                                                   "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["6", 0]}},
    }


def wf_qwen_edit(prompt, w, h, steps, seed, guidance, prefix, refs=()):
    """Qwen-Image-Edit-2511 (Apache 2.0): up to three reference pictures go
    straight into the edit encoder (image1 = identity, then pose/expression/
    garment guides or a second character). GGUF Q6_K weights + the Lightning
    8-step LoRA, so cfg stays 1.0 and `guidance` is ignored. Serves both the
    character and the edit mode: the picture is always drawn from an empty
    latent of the requested size, the references only condition it."""
    wf = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "qwen-image-edit-2511-Q6_K.gguf"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen/qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors", "strength_model": 1.0}},
        "5": {"class_type": "ModelSamplingAuraFlow", "inputs": {"shift": 3.0, "model": ["4", 0]}},
    }
    enc = {"clip": ["2", 0], "vae": ["3", 0]}
    for i, name in enumerate(list(refs)[:3], start=1):
        wf[f"img{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        enc[f"image{i}"] = [f"img{i}", 0]
    wf["6"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {**enc, "prompt": prompt}}
    wf["7"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {**enc, "prompt": ""}}
    wf["8"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}}
    wf["9"] = {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler",
                                                   "scheduler": "simple", "denoise": 1.0, "model": ["5", 0],
                                                   "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["8", 0]}}
    wf["10"] = {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}}
    wf["11"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["10", 0]}}
    return wf


QWEN_EDIT_FILES = ["unet/qwen-image-edit-2511-Q6_K.gguf", "text_encoders/qwen/qwen_2.5_vl_7b_fp8_scaled.safetensors",
                   "vae/qwen_image_vae.safetensors", "loras/Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors"]

MODELS = {
    "z-image-turbo": {
        "label": "Z-Image Turbo", "builder": wf_zimage, "steps": 8, "guidance": None, "lora_ok": True,
        "files": ["diffusion_models/z_image_turbo_bf16.safetensors", "text_encoders/qwen_3_4b_fp8_mixed.safetensors", "vae/ae.safetensors"],
        "speed": "about 10 s a picture", "license": "Apache 2.0 (commercial OK)",
        "best": "Following the prompt literally: scenes with several things in them, specific positions and counts, readable signs and text, quick iteration. Good general-purpose default.",
        "avoid": "Slightly less skin and fabric realism than FLUX on close portraits.",
        "tips": "8 steps is the sweet spot. Write plain sentences, subject first. Reroll the seed freely, it is cheap.",
        "install": "HF Comfy-Org/z_image_turbo: z_image_turbo_bf16 -> diffusion_models, qwen_3_4b_fp8_mixed -> text_encoders, ae -> vae",
    },
    "qwen-image": {
        "label": "Qwen-Image-Edit 2511 (character from a reference)", "builder": wf_qwen_edit, "guide_builder": wf_qwen_edit,
        "steps": 8, "guidance": None, "mode": "character", "fixed_settings": True, "max_refs": 3,
        "files": QWEN_EDIT_FILES,
        "speed": "about 50 s a picture (75 s on the first after a start); extra references cost nothing", "license": "Apache 2.0 (commercial OK)",
        "best": "Recurring characters for commercial work: identity, pose, expression and garment references go in as separate pictures (up to three) and the likeness holds across new poses, props and scenes. Flat, outlined styles (cards, stickers, comics) hold best.",
        "avoid": "Profile and three-quarter faces lose fine face markings; more than three references (the fourth is dropped). Photoreal skin is softer than FLUX.",
        "tips": "Name the anchors to keep ('same red flower, gold hoops, ID badge') and only what changes. Steps and guidance are fixed by the Lightning LoRA (8 steps, cfg 1); change the seed, not the settings.",
        "install": "HF unsloth/Qwen-Image-Edit-2511-GGUF Q6_K -> unet; lightx2v/Qwen-Image-Edit-2511-Lightning 8steps bf16 -> loras; Comfy-Org/Qwen-Image_ComfyUI vae -> vae; qwen_2.5_vl_7b_fp8_scaled -> text_encoders/qwen; custom node city96/ComfyUI-GGUF (installed 2026-10-04)",
    },
    "qwen-image-edit": {
        "label": "Qwen-Image-Edit 2511 edit (change an existing picture)", "builder": wf_qwen_edit,
        "steps": 8, "guidance": None, "mode": "edit", "fixed_settings": True, "max_refs": 3,
        "files": QWEN_EDIT_FILES,
        "speed": "about 50 s a picture", "license": "Apache 2.0 (commercial OK)",
        "best": "Editing a picture you already have when the result must be commercially usable: add a prop, change the scene or the banner text, bring in a second character from another picture (image 2).",
        "avoid": "Pixel-exact preservation of untouched areas: the picture is redrawn from the references, so small details can move.",
        "tips": "Say which image is which: 'the woman from image 1 ... the boy from image 2'. 8 steps, cfg 1 are fixed.",
        "install": "same files as Qwen-Image-Edit 2511",
    },
    "flux-kontext-edit": {
        "label": "FLUX.1 Kontext edit (change an existing picture)", "builder": wf_kontext_edit, "steps": 20, "guidance": 2.5, "mode": "edit",
        "files": ["diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors", "text_encoders/clip_l.safetensors", "text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors", "vae/ae.safetensors"],
        "speed": "about 90 s a picture", "license": "non-commercial",
        "best": "Editing a picture you already have: change the time of day, swap an outfit, add or remove an object, restyle it as a painting, keep everything else as is.",
        "avoid": "Rebuilding the whole scene; use text-to-image or a character for that.",
        "tips": "Name only what should change: 'make it night, keep everything else the same'. Guidance 2.5.",
        "install": "same files as FLUX.1 Kontext",
    },
    "flux-kontext": {
        "label": "FLUX.1 Kontext (character from a reference)", "builder": wf_kontext, "steps": 20, "guidance": 2.5, "mode": "character",
        "files": ["diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors", "text_encoders/clip_l.safetensors", "text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors", "vae/ae.safetensors"],
        "speed": "about 90 s with one reference, +55 s for each extra reference (pose, face, garment), up to 4", "license": "non-commercial",
        "best": "Recurring characters: pick a saved character and describe one moment: the action, the face, the clothes, the scene. The pose, expression and garments named in the prompt are matched to library pictures and added as references; the line under the prompt shows what will be used.",
        "avoid": "Extreme pose or camera-angle changes can soften the likeness. One character per picture.",
        "tips": "Say 'the same man/woman/dog from the reference' and describe only what changes, using the library's words for the action and the face (running, jumping, scared, angry...). Guidance rises to 4.0 / 28 steps by itself when a pose or face is requested. Clean, well-lit, front-facing references work best.",
        "install": "HF Comfy-Org/flux1-kontext-dev_ComfyUI fp8 -> diffusion_models; clip_l + t5xxl_fp8_e4m3fn_scaled -> text_encoders",
    },
    "flux1-dev": {
        "label": "FLUX.1 dev", "builder": wf_flux, "steps": 24, "guidance": 3.5,
        "files": ["checkpoints/flux1-dev-fp8.safetensors"],
        "speed": "about 35 s a picture", "license": "non-commercial",
        "best": "Photoreal people and portraits, skin, hair and fabric, product shots, painterly and editorial styles, moody lighting.",
        "avoid": "Keyword-list prompts (it drifts), text in the image, explicit anatomy (filtered training data), crowded scenes with many separate instructions.",
        "tips": "Guidance 3.5 for photos, 2.5 for painterly. 20 to 28 steps. Describe light and lens in words.",
        "install": "installed",
    },
    "toonyou": {
        "label": "ToonYou (SD 1.5)", "builder": wf_sd15, "steps": 25, "guidance": 7.0,
        "files": ["checkpoints/toonyou_beta6.safetensors"],
        "speed": "about 4 s a picture (max 768 px)", "license": "CreativeML OpenRAIL-M",
        "best": "Anime and cartoon characters, stickers, avatars, fast throwaway drafts. Works with the huge SD 1.5 LoRA library.",
        "avoid": "Realism, text, complex scenes, anything that needs the prompt followed closely.",
        "tips": "Keyword prompts are fine here. Guidance 6 to 8. Keep it square or 2:3.",
        "install": "installed",
    },
    "chroma": {
        "label": "Chroma", "builder": None, "steps": 26, "guidance": 4.0,
        "files": ["diffusion_models/chroma-unlocked-v50.safetensors", "text_encoders/t5xxl_fp8_e4m3fn.safetensors", "vae/ae.safetensors"],
        "speed": "about 30 s a picture", "license": "Apache 2.0",
        "best": "FLUX-quality output without the filtered training set: artistic nudity, horror, gore, anything FLUX draws badly. Also strong at painterly and anime styles.",
        "avoid": "Readable text, and it needs more steps than Z-Image.",
        "tips": "Uses a real negative prompt and CFG around 4. Treat like FLUX for prompt style.",
        "install": "HF lodestones/Chroma: chroma-unlocked-v50 -> diffusion_models; T5-XXL fp8 -> text_encoders; FLUX ae -> vae (about 14 GB)",
    },
    "flux2-klein-9b": {
        "label": "FLUX.2 klein 9B", "builder": None, "steps": 4, "guidance": None,
        "files": ["diffusion_models/flux-2-klein-9b.safetensors"],
        "speed": "about 6 s a picture", "license": "non-commercial (4B variant is Apache)",
        "best": "Layout and composition control, posters and text in the image, editing an existing picture with a reference.",
        "avoid": "Still new; fewer LoRAs and community fine-tunes.",
        "tips": "Distilled 4-step model. Great for fast drafts before a FLUX.1 or Chroma final.",
        "install": "HF black-forest-labs/FLUX.2-klein-9B (ComfyUI split files) + its text encoder",
    },
}
DEFAULT_IMAGE_MODEL = os.environ.get("DEFAULT_IMAGE_MODEL", "z-image-turbo")


def model_catalog() -> list[dict]:
    out = []
    for k, m in MODELS.items():
        out.append({"id": k, "label": m["label"], "installed": bool(m["builder"]) and _exists(*m["files"]),
                    "mode": m.get("mode", "txt2img"), "needs_character": m.get("mode") == "character",
                    "steps": m["steps"], "guidance": m["guidance"], "speed": m["speed"], "license": m["license"],
                    "best": m["best"], "avoid": m["avoid"], "tips": m["tips"], "install": m["install"]})
    return out


def pick_default() -> str:
    cat = {c["id"]: c for c in model_catalog()}
    if cat.get(DEFAULT_IMAGE_MODEL, {}).get("installed"):
        return DEFAULT_IMAGE_MODEL
    return next((c["id"] for c in cat.values() if c["installed"]), "flux1-dev")


def character_model() -> str:
    """Model for sheet views and derives: the commercially licensed Qwen pair when its files are installed, else FLUX Kontext."""
    return "qwen-image" if _exists(*QWEN_EDIT_FILES) else "flux-kontext"


def edit_model() -> str:
    return "qwen-image-edit" if _exists(*QWEN_EDIT_FILES) else "flux-kontext-edit"


# ----------------------------------------------------------- character sheet
# Pre-rendered views/expressions of a character, each made from the ORIGINAL
# reference (one hop, never chained). At generation time the best-matching view
# becomes the reference so Kontext does not have to fight the original pose.
SHEET = [
    # (tag, prompt, guidance, steps, mode). mode "edit" = Kontext edit workflow with the
    # reference as the starting picture (best for changing the face: the model otherwise keeps
    # the mouth it was given); mode "character" = reference-only, free composition (best for
    # smiles, full body and camera turns). Tested on 2026-10-03.
    ("front-sad",         "Make the face sad: remove the smile completely, mouth closed with the corners turned down, lower lip pushed out slightly, inner eyebrows raised and drawn together, eyes glistening and looking down, shoulders slumped. Keep everything else exactly the same.", 4.5, 28, "edit", "original"),
    ("front-neutral",     "Relax this face to completely neutral: eyebrows level and relaxed, eyes looking straight at the camera, mouth closed in a straight calm line with the corners neither up nor down, no smile, no frown, no tension. Keep everything else exactly the same.", 4.5, 28, "edit", "front-sad"),
    ("front-smile",       "The same {species} from the reference. Head and shoulders portrait facing the camera, warm smile.{suffix}", 2.5, 20, "character"),
    ("front-laugh",       "The same {species} from the reference. Head and shoulders portrait facing the camera, laughing out loud, eyes crinkled, mouth wide open.{suffix}", 3.0, 24, "character"),
    ("front-surprised",   "Make the face shocked and surprised: jaw dropped with the mouth hanging open in a round O shape, no smile, eyes opened as wide as possible with the whites showing, eyebrows raised high, forehead creased. Keep everything else exactly the same, hands out of frame.", 4.5, 28, "edit", "original"),
    ("front-angry",       "Make the face angry: replace the smile with a hard frown, mouth closed in a flat line with the corners pulled down or teeth gritted, eyebrows slammed down and pinched together, eyes narrowed in a glare, jaw clenched. Keep everything else exactly the same.", 4.5, 28, "edit", "original"),
    ("three-quarter",     "The same {species} from the reference. Rotate the camera so the character is seen at a three-quarter angle: head and shoulders turned about 45 degrees to the left, so we see more of the left cheek and ear, eyes looking off to the left, neutral expression.{suffix}", 4.0, 28, "character"),
    ("profile",           "The same {species} from the reference. Head and shoulders in strict side profile facing left, neutral expression.{suffix}", 3.0, 24, "character"),
    ("over-shoulder",     "The same {species} from the reference. Turn the camera around so we see the character from behind, from the waist up. The back and the back of the head face the camera, and the head is turned to look back over the right shoulder at the viewer with a slight smile.{suffix}", 4.0, 28, "character"),
    ("back-view",         "The same {species} from the reference. Turn the camera fully around so we see the character from directly behind, full body, standing: the back of the head, the back of the clothing and the legs face the camera, no face visible.{suffix}", 4.0, 28, "character"),
    ("full-body-standing","The same {species} from the reference. Full body standing facing the camera, arms relaxed at the sides, feet visible.{suffix}", 2.5, 20, "character"),
    ("walking-side",      "The same {species} from the reference. Full body in side view walking from right to left, mid stride, arms swinging.{suffix}", 3.0, 24, "character"),
    ("sitting",           "The same {species} from the reference. Full body sitting on a plain wooden stool, hands resting on the knees, facing the camera.{suffix}", 2.5, 20, "character"),
]
SHEET_TAGS = [t for t, *_ in SHEET]
SHEET_SUFFIX = " Plain light grey studio background, soft even light, nothing else in the picture. Keep the exact same face, hair, body, skin, colours and clothing as the reference."

_POSE_RULES = [
    ("over-shoulder", r"over (?:her|his|its|their|the) shoulder|look(?:s|ing)? back|glanc(?:es|ing) back|turn(?:s|ed|ing)? (?:her |his |its )?head back"),
    ("back-view",     r"(?<!lit )(?<!light )from behind|seen from the back|back view|walking away|back to (?:the )?camera|facing away"),
    ("walking-side",  r"\b(?:walking|walks|striding|strolling|strolls)"),   # running/jogging/sprinting live in the library as activities
    ("profile",       r"\bprofile\b(?!\s+(?:picture|photo|pic|page))|from the side|side view|seen from the side|sideways"),
    ("sitting",       r"\b(?:sitting|seated|sits|sat)\b"),
    ("full-body-standing", r"full[- ]body|full[- ]length|head to toe|standing"),
    ("three-quarter", r"three[- ]quarter|3/4|looking (?:away|off to the side)|glancing away"),
    ("front-laugh",   r"\blaugh"),
    ("front-surprised", r"surpris|shock|gasp|astonish|amazed|startled"),
    ("front-angry",   r"\bangry|furious|scowl|annoyed"),           # "mad scientist", "glaring sun" are scenes
    ("front-sad",     r"\bsad\b|crying|\bin tears\b|upset|weeping|gloomy|heartbroken"),
    ("front-smile",   r"smil|happy|grin|cheerful|joyful"),
]


def match_sheet(prompt: str, available: list[str]) -> str | None:
    """Pick the sheet view that best fits the prompt (pose words win over expression words)."""
    text = prompt.lower()
    for tag, rx in _POSE_RULES:
        if tag in available and re.search(rx, text):
            return tag
    return None


def sheet_dir(cid: str) -> Path:
    return CHARS / cid


def _num(v, default, cast):
    """A number from a request field, or the default when it is empty or garbage."""
    try:
        return cast(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _cid_ok(cid: str) -> bool:
    """A real saved character id (c + digits with a json next to it): never '..' or ''."""
    return bool(re.fullmatch(r"c\d+", cid or "")) and (CHARS / f"{cid}.json").exists()


# ----------------------------------------------------- uploaded views
# A character can carry any number of extra views (an expression library made
# elsewhere, e.g. an OpenArt expression sheet). They live next to the generated
# sheet views, with a label + keywords in views.json, and Auto prefers them.
def _views_meta(cid: str) -> dict:
    f = sheet_dir(cid) / "views.json"
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}


def _atomic_write(path: Path, text: str):
    """Readers never see a half-written file: write beside it, then rename over it."""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    tmp.write_text(text)
    os.replace(tmp, path)


@contextmanager
def _flock(path: Path):
    """Cross-process exclusive lock for a read-modify-write of one json file
    (several sessions and ingest scripts write the library index concurrently)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _save_views_meta(cid: str, meta: dict):
    sheet_dir(cid).mkdir(exist_ok=True)
    _atomic_write(sheet_dir(cid) / "views.json", json.dumps(meta, indent=1))


def _slug(label: str) -> str:
    t = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return t[:40] or "view"


_STOP = {"the", "and", "with", "face", "view", "side", "down", "up", "into", "from", "one", "two", "both", "while", "over",
         "under", "behind", "front", "back", "quarter", "left", "right", "his", "her", "their", "its", "for", "off", "out", "away",
         "forward", "on", "in", "at", "of", "to", "an", "very", "full", "body", "hand", "hands", "head", "heads", "arms", "arm",
         "legs", "leg", "feet", "foot", "eyes", "eye", "mouth", "big", "small", "little", "happy", "sad"}   # happy/sad stay as phrases


def _keywords(label: str) -> list[str]:
    words = [w for w in re.split(r"[^a-z]+", label.lower()) if len(w) > 2 and w not in _STOP]
    return sorted(set(words + ([label.lower().strip()] if " " in label.strip() else [])))


def views_of(cid: str) -> dict:
    """Every view the character has: generated sheet views + uploaded ones.
    {tag: {file, label, keywords, source}}"""
    d = sheet_dir(cid)
    out = {}
    for t in SHEET_TAGS:
        if (d / f"{t}.png").exists():
            out[t] = {"file": f"{t}.png", "label": t.replace("-", " "), "keywords": [], "source": "generated",
                      "kind": "expression" if t.startswith("front-") else "pose"}
    for t, m in _views_meta(cid).items():
        if (d / f"{t}.png").exists():
            # uploaded strips are expression sheets unless they say otherwise; derived views carry the library kind
            out[t] = {"file": f"{t}.png", "label": m.get("label", t), "keywords": m.get("keywords", []),
                      "source": m.get("source", "uploaded"), "kind": m.get("kind") or "expression"}
    return out


def _run_pil(code: str, data: bytes, timeout: int = 120, env: dict | None = None) -> bytes | None:
    """Run an image job in the ComfyUI venv (it has Pillow); stdin bytes -> stdout bytes."""
    try:
        r = subprocess.run([str(COMFY_DIR / "venv/bin/python"), "-c", code], input=data, capture_output=True, timeout=timeout,
                           env={**os.environ, **(env or {})})
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


_SPLIT_CODE = r"""
import sys, io, json, base64, os
from PIL import Image
grid = os.environ.get("GRID", "auto")            # auto | strip | none | CxR  (e.g. 4x2)
cap = float(os.environ.get("CAPTION", "0.06"))   # fraction of each cell's height to drop at the bottom (captions)
topc = float(os.environ.get("TOP", "0.03"))      # fraction dropped at the top (headers)
im = Image.open(io.BytesIO(sys.stdin.buffer.read())).convert("RGB")
g = im.convert("L"); w, h = im.size; px = g.load()
def line_runs(vals, thr=40):
    r, start = [], None
    for i, c in enumerate(list(vals) + [255]):
        if c < thr and start is None: start = i
        if c >= thr and start is not None: r.append((start, i)); start = None
    return r
_ys, _xs = range(0, h, 2), range(0, w, 2)
cols = [255.0 * (1.0 - sum(1 for y in _ys if px[x, y] < 110) / len(_ys)) for x in range(w)]   # ~0 only where a full-height dark line runs
rows = [255.0 * (1.0 - sum(1 for x in _xs if px[x, y] < 110) / len(_xs)) for y in range(h)]
def bounds(n, size, runs, exact=False):
    # lines hugging the edges are a border: they become the outer boundaries.
    # exact: the detected interior lines ARE the boundaries (auto mode);
    # otherwise expected boundaries at even spacing, snapped to the nearest dark line within 15%
    lo = max([z for a, z in runs if a <= size * 0.04], default=0)
    hi = min([a for a, z in runs if z >= size * 0.96], default=size)
    inner = [(a, z) for a, z in runs if a > size * 0.04 and z < size * 0.96]
    if exact and len(inner) == n - 1:
        return [lo] + inner + [hi]
    b = [lo]
    for i in range(1, n):
        exp = lo + i * (hi - lo) / n; near = [(abs((a + z) / 2 - exp), (a, z)) for a, z in inner if abs((a + z) / 2 - exp) < size * 0.15]
        b.append(min(near)[1] if near else (int(exp), int(exp)))
    b.append(hi)
    return b
if grid == "none":
    cells = [(0, 0, w, h)]
elif grid == "strip":
    cr = line_runs(cols); xs, x0 = [], None
    for x in range(w + 1):
        d = cols[x] < 40 if x < w else True
        if not d and x0 is None: x0 = x
        if d and x0 is not None:
            if x - x0 >= max(60, int(w * 0.12)): xs.append((x0, x))
            x0 = None
    cells = [(a, 0, b, h) for a, b in xs] or [(0, 0, w, h)]
else:
    if grid == "auto":
        nc = len([1 for a, z in line_runs(cols) if a > w * 0.04 and z < w * 0.96]) + 1
        nr = len([1 for a, z in line_runs(rows) if a > h * 0.04 and z < h * 0.96]) + 1
    else:
        nc, nr = [int(v) for v in grid.lower().split("x")]
    xb, yb = bounds(nc, w, line_runs(cols), grid == "auto"), bounds(nr, h, line_runs(rows), grid == "auto")
    cells = []
    for r in range(nr):
        for c in range(nc):
            x0 = xb[c] if isinstance(xb[c], int) else xb[c][1]; x1 = xb[c + 1] if isinstance(xb[c + 1], int) else xb[c + 1][0]
            y0 = yb[r] if isinstance(yb[r], int) else yb[r][1]; y1 = yb[r + 1] if isinstance(yb[r + 1], int) else yb[r + 1][0]
            cells.append((x0, y0, x1, y1))
out = []
for (x0, y0, x1, y1) in cells:
    p = im.crop((x0, y0, x1, y1)); pw, ph = p.size
    pg = p.convert("L"); pp = pg.load()
    def rmean(y): return sum(pp[x, y] for x in range(0, pw, 2)) / len(range(0, pw, 2))
    def cmean(x): return sum(pp[x, y] for y in range(0, ph, 2)) / len(range(0, ph, 2))
    top = 0
    while top < ph // 3 and rmean(top) < 165: top += 1
    bot = ph
    while bot > ph * 2 // 3 and rmean(bot - 1) < 165: bot -= 1
    left = 0
    while left < pw // 3 and cmean(left) < 165: left += 1
    right = pw
    while right > pw * 2 // 3 and cmean(right - 1) < 165: right -= 1
    p = p.crop((left + 4, top + 4, right - 4, bot - 4)); pw, ph = p.size
    p = p.crop((0, int(ph * topc), pw, int(ph * (1 - cap)))); pw, ph = p.size
    bg = p.getpixel((3, 3))
    side = max(pw, ph); sq = Image.new("RGB", (side, side), bg); sq.paste(p, ((side - pw) // 2, (side - ph) // 2))
    if side < 768: sq = sq.resize((768, 768), Image.LANCZOS)
    b = io.BytesIO(); sq.save(b, "PNG"); out.append(base64.b64encode(b.getvalue()).decode())
sys.stdout.write(json.dumps(out))
"""


def split_strip(data: bytes, split=True, caption: float = 0.06, top: float = 0.03) -> list[bytes]:
    """split: False/'none' = whole image; True/'strip' = one row cut on dark vertical
    lines; 'auto' or 'CxR' = grid. caption = fraction of each cell dropped at the bottom."""
    import base64
    grid = {False: "none", True: "strip", None: "none"}.get(split, split) if isinstance(split, (bool, type(None))) else str(split or "auto")
    if grid == "none":
        png = data if data.startswith(b"\x89PNG") else _to_png(data)
        return [png] if png else []
    res = _run_pil(_SPLIT_CODE, data, env={"GRID": grid, "CAPTION": str(caption), "TOP": str(top)})
    if not res:
        return []
    try:
        return [base64.b64decode(x) for x in json.loads(res)]
    except Exception:
        return []


def add_views(cid: str, images: list[bytes], labels: list[str], split="strip", caption: float = 0.06,
              kind: str = "expression", source: str = "uploaded", origin: str | None = None) -> list[dict]:
    """Add uploaded views (optionally splitting each strip/grid into panels). Labels are
    consumed in order across all panels; a label of 'skip' or '-' drops that panel;
    missing labels become 'view N'. The slow split runs before the lock; the index is
    re-read inside it so a derive job finishing meanwhile keeps its entry."""
    kind = kind if kind in ("expression", "pose", "activity") else "expression"
    panels, li = [], 0
    for data in images:
        for png in split_strip(data, split, caption):
            label = labels[li].strip() if li < len(labels) and labels[li].strip() else ""
            li += 1
            if label.lower() in ("skip", "-"):
                continue
            panels.append((label, png))
    added = []
    sheet_dir(cid).mkdir(exist_ok=True)
    with _flock(sheet_dir(cid) / ".lock"):
        meta = _views_meta(cid)
        existing = set(views_of(cid))
        for label, png in panels:
            label = label or f"view {len(existing) + 1}"
            tag = base = _slug(label)
            n = 2
            while tag in existing or tag in SHEET_TAGS:
                tag = f"{base}-{n}"; n += 1
            (sheet_dir(cid) / f"{tag}.png").write_bytes(png)
            meta[tag] = {"label": label, "keywords": _keywords(label), "source": source, "kind": kind, **({"from": origin} if origin else {})}
            existing.add(tag)
            added.append({"tag": tag, "label": label})
        _save_views_meta(cid, meta)
    return added


# ------------------------------------------------------------ shared library
# One library for the whole project: expressions, poses, activities, outfits,
# props. Each item is a picture (usually drawn with one "source" character) plus a
# label and keywords. For the source character the item is an identity reference.
# For any OTHER character it is a GUIDE: the generator gets [character | guide]
# side by side and is told to draw the left character in the right picture's pose,
# expression or outfit. A character can also "derive" its own copy of any item,
# which then becomes one of its own views (faster and more faithful afterwards).
LIB = Path(os.environ.get("LIBRARY_DIR") or HERE / "library")
LIB.mkdir(parents=True, exist_ok=True)
LIB_KINDS = ["expression", "pose", "activity", "outfit", "top", "pants", "shorts", "underwear", "socks", "footwear", "headwear",
             "accessory", "prop"]
GARMENT_KINDS = ["outfit", "top", "pants", "shorts", "underwear", "socks", "footwear", "headwear", "accessory", "prop"]  # worn/held: extra references, never the identity
# single garments shown front | side | back: (noun for the guide image, short name, where it is worn)
CLOTHING = {"top": ("top", "top", "on the upper body"), "pants": ("pair of trousers", "trousers", "on the legs"),
            "shorts": ("pair of shorts", "shorts", "on the legs"), "underwear": ("piece of underwear", "underwear", "as underwear"),
            "socks": ("pair of socks", "socks", "on both feet, with the socks visible")}
GARMENT_SLOT = {"shorts": "pants"}                                            # one item per body slot: never trousers AND shorts
MAX_GARMENTS = int(os.environ.get("MAX_GARMENTS", "2"))
CRITIC_AUTO = os.environ.get("CRITIC_AUTO", "1") != "0"          # vision model checks every finished picture
CRITIC_MODEL = os.environ.get("CRITIC_MODEL", "gemma4:31b-cloud")
LEARNER_MODEL = os.environ.get("LEARNER_MODEL", CRITIC_MODEL)
MAX_REFS = max(2, min(4, int(os.environ.get("MAX_REFS", "4"))))                               # identity + pose + expression + garment; each extra ref ~ +50 s
_ORD = ["first", "second", "third", "fourth", "fifth"]


# what the face does, in words: the picture alone is a weak instruction for a guide face
_EXPR_WORDS = [("angry|furious|mad|annoyed|stern", "eyebrows pulled down and together, eyes narrowed, mouth in a hard frown or teeth gritted, no smile"),
               ("scared|fear|afraid|terrified|worried|nervous", "eyes wide open, eyebrows raised and pinched together, mouth open in fear, no smile"),
               ("shock|surpris|gasp|jaw", "jaw dropped, mouth wide open, eyes as wide as possible, eyebrows high"),
               ("laugh", "mouth wide open laughing, eyes crinkled shut, head tilted back"),
               ("sad|frown|cry|tears|heartbroken", "mouth corners turned down, inner eyebrows raised, eyes glistening, no smile"),
               ("smil|happy|content", "a warm genuine smile, relaxed eyes"),
               ("bored|unimpressed|sleepy|tired|drool|yawn", "half-closed eyes, flat mouth, slack face"),
               ("disgust", "nose wrinkled, upper lip raised, head pulled back"),
               ("confus|skeptic|think", "one eyebrow raised, mouth pulled to one side"),
               ("love|kiss", "soft eyes, puckered lips"),
               ("pensive|thoughtful|contemplat|wistful|dreamy|daydream|lost in thought", "a thoughtful, faraway look: mouth closed and relaxed, no smile, brows slightly drawn, eyes unfocused"),
               ("sleep", "eyes closed, calm face"),
               ("neutral|calm|serious", "mouth closed in a straight line, relaxed level eyebrows, no smile")]


def _expr_words(label: str) -> str:
    for rx, words in _EXPR_WORDS:
        if re.search(rx, label.lower()):
            return ": " + words
    return ""


def guide_prompt(user_prompt: str, slots: list, identity_note: str = "") -> str:
    """Image 1 = the character. Each slot (kind, label[, mode]) is a further reference:
    mode 'own' = the same character (one of its own views), 'guide' = another
    character or an object. identity_note describes what image 1 already shows."""
    desc = ["The first image shows the character to draw" + (f", {identity_note}" if identity_note else "") + "."]
    instr = []
    outfit = clothing = False
    for i, slot in enumerate(slots, start=1):
        kind, label = slot[0], slot[1]
        own = len(slot) > 2 and slot[2] == "own"
        o = _ORD[i]
        if kind == "expression":
            desc.append(f"The {o} image shows the SAME character's face with the facial expression to use ({label})." if own else
                        f"The {o} image is an expression guide of a different person ({label}).")
            instr.append(f"with exactly the facial expression of the {o} image ({label}{_expr_words(label)}), not the expression of the first image")
        elif kind == "pose":
            desc.append(f"The {o} image shows the SAME character in the body pose to use ({label})." if own else
                        f"The {o} image is a pose guide of a different person ({label}).")
            instr.append(f"in exactly the body pose, hand positions and camera angle of the {o} image ({label}), not the pose of the first image")
        elif kind == "activity":
            desc.append(f"The {o} image shows the SAME character doing the action to draw ({label})." if own else
                        f"The {o} image is an action guide of a different person ({label}).")
            instr.append(f"doing exactly the action of the {o} image ({label}) with the same body pose, camera angle and props, not the pose of the first image")
        elif kind == "footwear":
            desc.append(f"The {o} image shows one pair of shoes ({label}) from the front, side and back.")
            instr.append(f"wearing exactly that footwear from the {o} image on both feet, with the feet and shoes visible")
        elif kind == "outfit":
            desc.append(f"The {o} image shows the SAME character wearing the outfit to use ({label}), from the front, side and back."
                        if own else f"The {o} image shows an outfit ({label}) on a different person.")
            instr.append(f"dressed in exactly the clothing of the {o} image")
            outfit = True
        elif kind in CLOTHING:
            noun, short, where = CLOTHING[kind]
            desc.append(f"The {o} image shows one {noun} ({label}) from the front, side and back.")
            this = "those" if short in ("trousers", "shorts", "socks") else "that"
            instr.append(f"wearing exactly {this} {short} from the {o} image {where}, with the same cut, colour, pattern and details "
                         "unless the prompt names another colour")
            clothing = True
        elif kind == "headwear":
            desc.append(f"The {o} image shows one piece of headwear ({label}) by itself from the front, side and back, worn by no one.")
            instr.append(f"wearing exactly that headwear from the {o} image on the head, fitted naturally, with the head and the headwear clearly visible")
        elif kind == "accessory":
            desc.append(f"The {o} image shows an accessory ({label}).")
            instr.append(f"wearing or carrying exactly the accessory of the {o} image")
        else:
            desc.append(f"The {o} image shows an object ({label}).")
            instr.append(f"holding or using exactly the object of the {o} image")
    keep = "keeping the first character's own face, hair and body" + ("" if outfit else " and the rest of their own clothing" if clothing
                                                                       else " and clothing")
    return (" ".join(desc) + " Make ONE single picture of the first character only, " + ", ".join(instr) + ", " + keep +
            ". One frozen moment, a single frame. Never draw any other person from the guide images. " + user_prompt)



def _lib_index() -> dict:
    f = LIB / "index.json"
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}


def _save_lib_index(idx: dict):
    _atomic_write(LIB / "index.json", json.dumps(idx, indent=1))


def library_items() -> dict:
    idx = _lib_index()
    return {t: m for t, m in idx.items() if (LIB / f"{t}.png").exists()}


_COMPOSE_CODE = r"""
import sys, io, json, base64
from PIL import Image
parts = [Image.open(io.BytesIO(base64.b64decode(x))).convert("RGB") for x in json.load(sys.stdin)]
h = max(p.height for p in parts); gap = 24
parts = [p.resize((int(p.width * h / p.height), h)) if p.height != h else p for p in parts]
bg = parts[0].getpixel((3, 3))
out = Image.new("RGB", (sum(p.width for p in parts) + gap * (len(parts) - 1), h), bg)
x = 0
for p in parts:
    out.paste(p, (x, 0)); x += p.width + gap
b = io.BytesIO(); out.save(b, "PNG"); sys.stdout.buffer.write(b.getvalue())
"""


def compose_row(pngs: list[bytes]) -> bytes | None:
    """Several views of one thing (front | side | back) become one wide guide image."""
    import base64
    if len(pngs) == 1:
        return pngs[0]
    return _run_pil(_COMPOSE_CODE, json.dumps([base64.b64encode(p).decode() for p in pngs]).encode())


def add_library(images: list[bytes], labels: list[str], kind: str, source: str = "", split="auto", caption: float = 0.15,
                group: int = 1, top: float = 0.03, keywords: list | None = None) -> list[dict]:
    """labels/keywords are consumed per ITEM (= per group of `group` consecutive panels).
    Splitting and compositing happen before the lock; the index is re-read inside it and
    only the new entries are added, so concurrent ingests never overwrite each other."""
    kind = kind if kind in LIB_KINDS else "pose"
    group = max(1, min(12, int(group or 1)))
    items, li = [], 0
    for data in images:
        panels = split_strip(data, split, caption, top)
        for g in range(0, len(panels), group):
            chunk = panels[g:g + group]
            label = labels[li].strip() if li < len(labels) and labels[li].strip() else ""
            kws = keywords[li] if keywords and li < len(keywords) and keywords[li] else None
            li += 1
            if label.lower() in ("skip", "-"):
                continue
            png = compose_row(chunk) if len(chunk) > 1 else chunk[0]
            if png:
                items.append((label, kws, png, len(chunk)))
    added = []
    with _flock(LIB / ".lock"):
        idx = _lib_index()
        for label, kws, png, views in items:
            label = label or f"{kind} {len(idx) + 1}"
            tag = base = _slug(label); n = 2
            while tag in idx:
                tag = f"{base}-{n}"; n += 1
            (LIB / f"{tag}.png").write_bytes(png)
            idx[tag] = {"kind": kind, "label": label, "keywords": kws or _keywords(label), "source": source, "file": f"{tag}.png",
                        "views": views}
            added.append({"tag": tag, "label": label, "kind": kind})
        _save_lib_index(idx)
    return added


def lib_to_comfy(tag: str) -> str:
    src = LIB / f"{tag}.png"
    name = f"picgen-lib-{tag}.png"
    dst = COMFY_DIR / "input" / name
    dst.parent.mkdir(exist_ok=True)
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        shutil.copy(src, dst)
    return name


def _stem(w: str) -> str:
    """Tiny stemmer so 'boots'/'boot', 'shoes'/'shoe', 'sitting'/'sits' agree
    (and 'wellies' never collapses onto 'well')."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
    elif len(w) > 4 and (w.endswith(("ches", "shes")) or (w.endswith("es") and w[-3] in "sxz")):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeioulsz":   # sitt -> sit, runn -> run
        w = w[:-1]
    return w


def _stems(w: str) -> frozenset:
    """All plausible stems of a word: 'riding' -> {rid, ride}, 'rides' -> {ride}."""
    base = _stem(w)
    out = {base}
    if w.endswith(("ing", "ed")) and len(base) >= 3:
        out.add(base + "e")
    return frozenset(out)


def _toks(text: str) -> list[frozenset]:
    text = re.sub(r"\btie[- ]dyed?\b", "tiedye", text.lower())        # "tie-dye shirt" is not a necktie
    return [_stems(t) for t in re.findall(r"[a-z0-9]+", text)]


def _tok_eq(a: frozenset, b: frozenset) -> bool:
    return not a.isdisjoint(b)


def _phrase_in(toks: list[frozenset], kt: list[frozenset]) -> bool:
    n = len(kt)
    return any(all(_tok_eq(toks[i + j], kt[j]) for j in range(n)) for i in range(len(toks) - n + 1))


def _kw_score(text: str, keywords: list[str]) -> float:
    """Keyword match for expressions/poses/activities. Multi-word keywords (the
    label itself) score as phrases; coverage (share of the item's single-word
    keywords that hit) breaks ties."""
    toks = _toks(text)
    score, hits, singles = 0.0, 0, 0
    for kw in keywords:
        kw = kw.lower().strip()
        if not kw:
            continue
        kt = _toks(kw)
        if not kt:
            continue
        if len(kt) > 1:
            if _phrase_in(toks, kt):
                score += 3; hits += 1
        else:
            singles += 1
            if any(_tok_eq(kt[0], t) for t in toks):
                score += 2 if len(kw) > 3 else 1; hits += 1
    return score + (2.0 * hits / max(1, singles) if hits else 0)


def _garment_score(toks: list[frozenset], keywords: list[str]) -> float:
    """Phrase = 3, strong word = 3, weak word (written '!boot') = 1.5, so a bare
    'boots' or 'flat' never pulls in a shoe on its own."""
    score = 0.0
    seen = []
    for kw in keywords:
        kw = kw.lower().strip()
        if not kw:
            continue
        weak = kw.startswith("!")
        kt = _toks(kw.lstrip("!"))
        if not kt:
            continue
        if len(kt) > 1:
            if _phrase_in(toks, kt) and not any(len(k) == len(kt) and all(_tok_eq(a, b) for a, b in zip(k, kt)) for k in seen):
                score += 1.5 if weak else 3; seen.append(kt)
        elif any(_tok_eq(kt[0], t) for t in toks) and not any(len(k) == 1 and _tok_eq(k[0], kt[0]) for k in seen):
            score += 1.5 if weak else 3; seen.append(kt)
    return score


def match_library(prompt: str, kinds: tuple = ("expression", "pose", "activity"), with_score: bool = False):
    text = " " + re.sub(r"[^a-z0-9 ]+", " ", prompt.lower()) + " "
    best, best_score = None, 0
    for tag, m in library_items().items():
        if m.get("kind", "pose") not in kinds:
            continue
        sc = _kw_score(text, m.get("keywords", []))
        if sc > best_score:
            best, best_score = tag, sc
    return (best, best_score) if with_score else best


ACCESSORY_CATEGORIES = ("rings", "bracelets", "watches", "necklaces", "earrings", "eyewear", "bags", "tech", "misc")


def _garment_slot(kind: str, m: dict) -> str:
    """One worn item per body slot. Trousers and shorts share a slot; accessories get one
    slot per category (a watch AND sunglasses, but not two watches): the category keyword
    if the item carries one, else the last word of its label ("…watch", "…necklace")."""
    kind = GARMENT_SLOT.get(kind, kind)
    if kind != "accessory":
        return kind
    cat = next((kw for kw in m.get("keywords", []) if kw in ACCESSORY_CATEGORIES), None)
    if not cat:
        words = re.findall(r"[a-z0-9]+", str(m.get("label", "")).lower())
        cat = _stem(words[-1]) if words else ""
    return f"accessory:{cat}"


def match_garments(prompt: str, limit: int = MAX_GARMENTS) -> list[str]:
    """Best library item per garment slot named in the prompt (score >= 3); a slot is
    the kind, except that trousers and shorts share one."""
    toks = _toks(prompt)
    best = {}
    for tag, m in library_items().items():
        k = m.get("kind", "")
        if k not in GARMENT_KINDS:
            continue
        kws = [kw for kw in m.get("keywords", []) if kw not in ACCESSORY_CATEGORIES]          # "watches" is a category, not a match
        sc = _garment_score(toks, kws)
        if sc >= 3 and _garment_score(toks, [kw for kw in kws if not kw.startswith("!")]) < 3:
            continue                                            # weak words only rank, never qualify ("watches the waves" != a fitness band)
        k = _garment_slot(k, m)
        if sc >= 3 and sc > best.get(k, (None, 0))[1]:
            best[k] = (tag, sc)
    lib = library_items()
    raw = " " + " ".join(re.findall(r"[a-z0-9]+", prompt.lower())) + " "
    raw = re.sub(r"\bshades of\b|\bon (?:his|her|their|the|my) heels\b|\bboots? up\b|\bbottle caps?\b|\bknee ?caps?\b|\bice caps?\b|\bwatch(?:es|ing)? (?:the|a|his|her)\b",
                 " ", raw)                                       # idioms that only look like clothing
    for kind, words, tag in GENERIC_GARMENTS:                 # "sneakers" alone -> a default sneaker (exact word, so "flat" != "flats")
        if tag not in lib:
            continue
        kind = _garment_slot(kind, lib[tag])
        if kind not in best and any(f" {w} " in raw for w in words):
            best[kind] = (tag, 2.5)
    ranked = sorted(best.values(), key=lambda x: -x[1])
    return [t for t, _ in ranked[:limit]]


GENERIC_GARMENTS = [
    ("footwear", ["sneakers", "sneaker", "trainers", "tennis shoes"], "green-low-top-canvas-sneaker"),
    ("footwear", ["boots"], "tan-leather-work-boot"),
    ("footwear", ["sandals"], "slide-sandal"),
    ("footwear", ["heels", "high heels", "pumps"], "black-classic-pump"),
    ("footwear", ["dress shoes", "formal shoes"], "oxford-dress-shoe"),
    ("footwear", ["slippers"], "green-house-slipper"),
    ("footwear", ["flats", "flat shoes"], "pink-ballet-flat"),
    ("footwear", ["clogs"], "brown-foam-clog"),
    ("footwear", ["loafers"], "penny-loafer"),
    ("top", ["t shirt", "t shirts", "tshirt", "tee", "tee shirt"], "white-t-shirt"),
    ("socks", ["socks", "sock"], "plain-white-crew-sock"),
    ("underwear", ["underwear", "undies", "underpants"], "boxer-briefs"),
    ("headwear", ["hat", "hats"], "baseball-cap"),
    ("headwear", ["cap", "caps"], "baseball-cap"),
    ("headwear", ["helmet", "helmets"], "bicycle-helmet"),
    ("headwear", ["headset"], "aviator-headset"),
    ("headwear", ["visor"], "sun-visor"),
    ("accessory", ["sunglasses", "shades"], "wayfarer-sunglasses"),
    ("accessory", ["watch", "wristwatch"], "analog-watch-with-brown-leather-strap"),
    ("accessory", ["necklace"], "pendant-necklace-with-a-small-stone"),
    ("accessory", ["earrings", "earring"], "gold-hoop-earrings"),
]


def queue_derive(cid: str, tags: list[str], species: str = "character") -> int:
    """Render this character's own copy of library items (guide workflow, studio background)."""
    lib = library_items(); have = views_of(cid); pend = set(sheet_pending(cid)); n = 0
    for tag in tags:
        m = lib.get(tag)
        if not m or tag in have or tag in pend:
            continue
        k = m.get("kind", "pose")
        if k == "expression":
            prompt = f"The same {species} from the reference. Head and shoulders portrait facing the camera, {m['label']} expression." + SHEET_SUFFIX
        elif k in GARMENT_KINDS:
            prompt = f"The same {species} from the reference. Full body standing facing the camera, wearing {m['label']}, the whole body and the feet visible." + SHEET_SUFFIX
        else:
            prompt = f"The same {species} from the reference, {m['label']}." + SHEET_SUFFIX
        job = {"id": f"{int(time.time())}-{random.randint(1000, 9999)}", "prompt": prompt, "model": character_model(),
               "characters": [cid], "edit_of": None, "style": "none", "w": 1024, "h": 1024, "steps": 28, "guidance": 4.0,
               "seed": random.randint(1, 2**31), "status": "queued", "at": time.strftime("%Y-%m-%d %H:%M"),
               "sheet_for": cid, "sheet_tag": tag, "reference": f"lib:{tag}", "lib_meta": {"label": m["label"], "keywords": m["keywords"], "kind": m["kind"]}}
        with lock:
            state["queue"].append(job)
        n += 1
    save_queue()
    return n


# camera-turn views: an own view if the character has one, else the library's version
STRONG = ("over-shoulder", "back-view", "profile")                      # camera turns only; walking competes with the library
LIB_ALIASES = {"over-shoulder": ["over-shoulder", "quarter-back-view-left"],
               "back-view": ["back-view", "walking-away-back-view", "back-view-standing"],
               "walking-side": ["walking-side", "walking-side-view-stride"],
               "profile": ["profile", "left-profile-standing", "right-profile-standing"]}


def match_view(prompt: str, cid: str) -> str | None:
    """Best reference for a prompt. Camera turns win outright; then the character's
    own uploaded/derived views and the shared library compete on keyword score;
    then the weak generated-pose rules; else None (= the original reference)."""
    views = views_of(cid)
    lib = library_items()
    text = " " + re.sub(r"[^a-z0-9 ]+", " ", prompt.lower()) + " "
    for tag, rx in _POSE_RULES:
        if tag in STRONG and re.search(rx, text):
            if tag in views:
                return tag
            for alias in LIB_ALIASES.get(tag, []):
                if alias in lib and lib[alias].get("kind") in ("pose", "activity"):
                    return f"lib:{alias}"
    own_best, own_score = None, 0
    for tag, v in views.items():
        if v["source"] == "generated":
            continue
        sc = _kw_score(text, v.get("keywords", []))
        if sc > own_score:
            own_best, own_score = tag, sc
    lib_best, lib_score = match_library(prompt, with_score=True)
    if own_best and own_score >= 2 and own_score >= lib_score:
        return own_best
    if lib_best and lib_score >= 2:
        return lib_best if lib_best in views else f"lib:{lib_best}"
    for tag, rx in _POSE_RULES:
        if tag in views and tag not in STRONG and re.search(rx, text):
            return tag
    return None



# ------------------------------------------------------------ reference planning
# A picture of a saved character can use up to MAX_REFS reference pictures: the
# identity, a body pose or action, a facial expression, and worn garments.
# plan_refs() decides all of them from the prompt (or the explicit picks) and is
# shared by the worker and GET /api/plan, so the page shows the same decision
# the generator will make.
EXPR_KINDS = ("expression",)
POSE_KINDS = ("pose", "activity")


# words that appear in many labels and in almost every prompt: never a reason to pick a view
_MATCH_STOP = {"pose", "poses", "face", "faces", "facing", "look", "looks", "looking", "camera", "portrait", "seen", "three",
               "shoulder", "shoulders", "high", "holding", "hold", "holds", "view", "side", "front", "back", "full", "body", "same",
               "character", "reference", "picture", "image", "scene", "style", "cartoon", "wearing", "wears", "standing", "stands",
               "stand", "sit", "sits", "sitting", "seated",     # bare sit/stand go to the generated views; activities need their own words
               "wide", "shot", "eyed", "good", "all", "job", "yes", "sir", "too", "hard", "well", "big", "small", "little"}
# prompt words the library spells differently
_EXPAND = [(r"\bsprint\w*|\bdash(?:es|ing)?\b|\bflee(?:s|ing)?\b|\bfled\b", " running"),
           (r"\bterrified|\bpetrified|\bhorrified|\bafraid", " scared"),
           (r"\bfurious|\blivid|\benraged|\bfuming", " angry"),
           (r"\bhappy|\bbeaming|\bcheerful|\bgrinning|\bgrins?\b|\bjoyful", " smiling"),
           (r"\bsad\b|\bsadly|\bweep\w*|\bsob(?:s|bing)?\b|\bin tears\b|\bheartbroken|\bgloomy", " sad frown"),
           (r"\bchuckl\w*|\bgiggl\w*|\bcracks up|\blol\b", " laughing"),
           (r"\basleep\b|\bsnooz\w*|\bnapping\b|\btak(?:es|ing) a nap\b", " sleeping"),
           (r"\bexhausted|\bworn out|\bknackered", " tired"),
           (r"\bstrolls?\b|\bstrolling|\bwander\w*", " walking")]


_NEGATE = [r"\b(?:the|ocean|sea|big|crashing|rolling) waves?\b", r"\bshock absorbers?\b", r"\bwide[- ]angle\b"]   # scene words that look like a view


def _expand(text: str) -> str:
    for rx in _NEGATE:
        text = re.sub(rx, " ", text)
    for rx, extra in _EXPAND:
        if re.search(rx, text):
            text += extra
    return text + " "


def _view_score(text: str, m: dict) -> float:
    """How well one pose/activity/expression item fits a prompt. Phrases 3; real single
    words 2 (generic words never count); +1.5 when the label's head word is in the
    prompt ('jumping' for 'jumping arms up'); +1.5 when the whole label is; coverage
    bonus up to 2. Threshold 3: a lone word like 'beach' cannot pick an activity."""
    toks = _toks(text)
    score, hits, singles = 0.0, 0, 0
    for kw in m.get("keywords", []):
        kw = str(kw).lower().strip()
        kt = _toks(kw)
        if not kt:
            continue
        if len(kt) > 1:
            if _phrase_in(toks, kt):
                score += 3; hits += 1
        elif kw not in _MATCH_STOP:
            singles += 1
            if any(_tok_eq(kt[0], t) for t in toks):
                score += 2 if len(kw) > 3 else 1; hits += 1
    if hits >= 2:                                   # coverage rewards several of the item's words, never one lone noun
        score += 2.0 * hits / max(1, singles)
    lw = re.findall(r"[a-z0-9]+", str(m.get("label", "")).lower())
    if lw:
        if _phrase_in(toks, _toks(" ".join(lw))):
            score += 1.5
        head = _stems(lw[0])
        generic = lw[0] in _MATCH_STOP or lw[0] in _STOP
        if (not generic or hits >= 1) and any(_tok_eq(head, t) for t in toks):   # "sitting at a desk": stop head + a real hit
            score += 1.5
    return score


def _best_by_kind(text: str, cid: str, kinds: tuple, views: dict, lib: dict):
    """Best own view (uploaded/derived) or library item of these kinds for the prompt:
    (ref, label, mode, score) or None. Own views win ties; score threshold 3."""
    best = None

    def score(m):
        return _view_score(text, m)

    for tag, v in views.items():
        if v.get("source") == "generated" or v.get("kind") not in kinds:
            continue
        sc = score(v)
        if sc >= 3 and (best is None or sc > best[3]):
            best = (tag, v["label"], "own", sc)
    camera_only = {alias for t in STRONG for alias in LIB_ALIASES.get(t, [])}   # back/profile/over-shoulder: chosen by the camera rules only
    for tag, m in lib.items():
        if m.get("kind") not in kinds or tag in camera_only or not (LIB / f"{tag}.png").exists():
            continue
        sc = score(m)
        if sc >= 3 and (best is None or sc > best[3]):
            best = (f"lib:{tag}", m["label"], "own" if cid and m.get("source") == cid else "guide", sc)
    return best


def _slot_of_ref(ref: str, cid: str, views: dict, lib: dict):
    """(slot, label, mode) for an explicit reference pick; None for original/unknown."""
    if ref.startswith("lib:"):
        t = Path(ref[4:]).name; m = lib.get(t)
        if not m or not (LIB / f"{t}.png").exists():
            return None
        k = m.get("kind", "pose")
        if k in GARMENT_KINDS:
            return ("garment", m["label"], "guide")
        return ("expression" if k in EXPR_KINDS else "pose", m["label"], "own" if m.get("source") == cid else "guide")
    v = views.get(Path(ref).name)
    if not v:
        return None
    return ("expression" if v.get("kind") in EXPR_KINDS else "pose", v["label"], "own")


def _rule_hit(rx: str, text: str) -> str:
    m = re.search(rx, text)
    return f"'{m.group(0).strip()}'" if m else ""


_FRONT_FALLBACK = {"front-sad": ["sad-frown", "sad", "crying-sad", "crying"], "front-smile": ["smiling", "happy-smile", "smile", "content"],
                   "front-laugh": ["big-laugh", "laughing-hard", "laughing", "laugh"], "front-angry": ["angry", "furious", "serious-angry"],
                   "front-surprised": ["surprised", "shocked", "shocked-jaw-drop", "gasp"], "front-neutral": ["calm-neutral", "neutral"]}


OUTFIT_COVERS = ("top", "pants", "shorts", "socks", "footwear")   # clothing kinds a ready-made outfit replaces


def _outfit_first(tags: list, o: str, keep: list) -> list:
    """The outfit is the most valuable reference (whole body): it goes right after anything picked by hand,
    so the reference budget drops an accessory before it drops the clothes."""
    return [t for t in tags if t in keep] + [o] + [t for t in tags if t != o and t not in keep]


def resolve_outfits(gtags: list, lib: dict, cid: str, keep: list) -> tuple[list, list]:
    """Ready-made outfits first. gtags = matched/picked garment tags in rank order; keep = tags
    picked by hand (never dropped). Returns (tags, notes).
    1. An outfit named in the prompt covers the top, trousers/shorts, socks and shoes: the
       separate items it already contains are dropped. If the prompt also names clothing that
       is NOT part of that outfit, the person asked for something else: the outfit is dropped.
    2. No outfit named, but 2+ clothing items that all belong to ONE of this character's own
       outfits: that outfit replaces them (one finished full-body reference instead of several)."""
    notes = []
    comps = lambda t: set((lib[t].get("components") or {}).values())
    clothes = [t for t in gtags if lib[t].get("kind") in OUTFIT_COVERS]
    outfits = [t for t in gtags if lib[t].get("kind") == "outfit"]
    for o in outfits[1:]:                                   # one outfit per picture
        if o not in keep:
            gtags = [t for t in gtags if t != o]
    outfits = [o for o in outfits if o in gtags]
    if outfits:
        o = outfits[0]
        alien = [t for t in clothes if t not in comps(o)]
        if alien and o not in keep:
            notes.append(f"{lib[o]['label']} not used: the prompt also names "
                         + ", ".join(lib[t]["label"] for t in alien) + ", which is not part of it")
            gtags = [t for t in gtags if t != o]                # the named clothing may still be one of the ready-made outfits
        else:
            drop = [t for t in clothes if t in comps(o) and t not in keep]
            if drop:
                notes.append(f"{lib[o]['label']} already includes " + ", ".join(lib[t]["label"] for t in drop))
            return _outfit_first([t for t in gtags if t not in drop], o, keep), notes
    if cid and len(clothes) >= 2:
        want = set(clothes)
        cands = [t for t, m in lib.items() if m.get("kind") == "outfit" and m.get("source") == cid and want <= comps(t)]
        if cands:
            o = min(cands, key=lambda t: (len(comps(t)), lib[t].get("number", 999), t))
            drop = [t for t in clothes if t not in keep]
            out = [t for t in gtags if t not in drop]
            notes.append(f"used the ready-made {lib[o]['label']} (" + ", ".join(lib[t]["label"] for t in clothes) + ")")
            return _outfit_first(out, o, keep), notes
    return gtags, notes


def plan_refs(prompt: str, cid: str, want: str = "auto", garments="auto", auto: bool = True) -> dict:
    """identity + pose + expression + garments for one picture. want = auto | original |
    <own view tag> | lib:<tag>; an explicit pick fills its slot and Auto fills the rest.
    'original' means the plain reference: no pose or expression view at all.
    auto=False (sheet and derive jobs): only the explicit pick, nothing matched from the text."""
    views = views_of(cid); lib = library_items()
    text = _expand(" " + re.sub(r"[^a-z0-9 ]+", " ", prompt.lower()) + " ")
    plan = {"identity": {"ref": "original", "label": "original", "mode": "original"}, "pose": None, "expression": None,
            "garments": [], "notes": []}
    want = want or "auto"
    explicit_garments = []
    if not auto and garments in ("auto", None):
        garments = []
    if want not in ("auto", "original"):
        slot = _slot_of_ref(want, cid, views, lib)
        if slot is None:
            plan["notes"].append(f"reference '{want}' does not exist any more; Auto used instead")
            want = "auto"
        elif slot[0] == "garment":
            explicit_garments.append(Path(want[4:]).name); want = "auto"
        else:
            plan[slot[0]] = {"ref": want if want.startswith("lib:") else Path(want).name, "label": slot[1], "mode": slot[2],
                             "why": "picked by hand"}
    auto = auto and want != "original"
    if auto and not plan["pose"]:
        # camera turns win outright: the character's own view if it has one, else the library's
        for tag, rx in _POSE_RULES:
            if tag in STRONG and re.search(rx, text):
                if tag in views:
                    plan["pose"] = {"ref": tag, "label": views[tag]["label"], "mode": "own", "why": _rule_hit(rx, text)}
                    break
                for alias in LIB_ALIASES.get(tag, []):
                    if alias in lib and lib[alias].get("kind") in POSE_KINDS and (LIB / f"{alias}.png").exists():
                        plan["pose"] = {"ref": f"lib:{alias}", "label": lib[alias]["label"],
                                        "mode": "own" if lib[alias].get("source") == cid else "guide", "why": _rule_hit(rx, text)}
                        break
                if plan["pose"]:
                    break
    if auto and not plan["pose"]:
        b = _best_by_kind(text, cid, POSE_KINDS, views, lib)
        if b:
            plan["pose"] = {"ref": b[0], "label": b[1], "mode": b[2], "why": "keywords"}
    if auto and not plan["pose"]:
        for tag, rx in _POSE_RULES:          # weak generated views: sitting, full body, three-quarter
            if tag in views and tag not in STRONG and not tag.startswith("front-") and re.search(rx, text):
                plan["pose"] = {"ref": tag, "label": views[tag]["label"], "mode": "own", "why": _rule_hit(rx, text)}
                break
    if auto and not plan["expression"]:
        b = _best_by_kind(text, cid, EXPR_KINDS, views, lib)
        if b:
            plan["expression"] = {"ref": b[0], "label": b[1], "mode": b[2], "why": "keywords"}
        else:
            for tag, rx in _POSE_RULES:
                if not tag.startswith("front-") or not re.search(rx, text):
                    continue
                if tag in views:
                    plan["expression"] = {"ref": tag, "label": views[tag]["label"], "mode": "own", "why": _rule_hit(rx, text)}
                    break
                for alt in _FRONT_FALLBACK.get(tag, []):          # "happy"/"sad": the generated face is gone, use the library's
                    if alt in views and views[alt].get("source") != "generated":
                        plan["expression"] = {"ref": alt, "label": views[alt]["label"], "mode": "own", "why": _rule_hit(rx, text)}
                        break
                    if alt in lib and lib[alt].get("kind") in EXPR_KINDS and (LIB / f"{alt}.png").exists():
                        plan["expression"] = {"ref": f"lib:{alt}", "label": lib[alt]["label"],
                                              "mode": "own" if lib[alt].get("source") == cid else "guide", "why": _rule_hit(rx, text)}
                        break
                if plan["expression"]:
                    break
    # identity: an own full-body pose view is the best identity; else the original
    p, e = plan["pose"], plan["expression"]
    # (a face-only view is never the identity on its own: it is a cropped head, so the model
    # had to invent the top of the head and the body -> "skin-coloured hair" on a bald character)
    if p and p["mode"] == "own":
        plan["identity"] = {"ref": p["ref"], "label": p["label"], "mode": "own"}
    # garments: explicit list or matched from the prompt, within the reference budget
    gtags = match_garments(prompt, limit=8) if garments in ("auto", None) else [Path(str(t)).name.removeprefix("lib:") for t in garments]
    gtags = explicit_garments + [t for t in gtags if t not in explicit_garments]
    gtags = [t for t in gtags if t in lib and lib[t].get("kind") in GARMENT_KINDS and (LIB / f"{t}.png").exists()]
    gtags, onotes = resolve_outfits(gtags, lib, cid, explicit_garments)
    plan["notes"].extend(onotes)
    extra = (1 if p and p["ref"] != plan["identity"]["ref"] else 0) + (1 if e and e["ref"] != plan["identity"]["ref"] else 0)
    room = max(0, min(MAX_GARMENTS, MAX_REFS - 1 - extra))
    if len(gtags) > room:
        plan["notes"].append(f"only {room} garment{'s' if room != 1 else ''} fit in this picture ({MAX_GARMENTS} max, {MAX_REFS} references in all); dropped "
                             + ", ".join(lib[t]["label"] for t in gtags[room:]))
    plan["garments"] = [{"tag": t, "label": lib[t]["label"], "kind": lib[t]["kind"],
                         "mode": "own" if cid and lib[t].get("source") == cid else "guide"} for t in gtags[:room]]
    plan["ref_count"] = 1 + extra + len(plan["garments"])
    plan["boost"] = bool(p or e)
    return plan


def _ref_file(ref: str, cid: str) -> str:
    return lib_to_comfy(ref[4:]) if ref.startswith("lib:") else char_to_comfy(cid, ref)


def refs_for_plan(plan: dict, cid: str):
    """ComfyUI input names in prompt order, the slots guide_prompt describes, and
    the note about what the identity picture already shows."""
    ident = plan["identity"]
    refs, slots, note = [_ref_file(ident["ref"], cid)], [], ""
    p, e = plan["pose"], plan["expression"]
    if p and ident["ref"] == p["ref"]:
        note = f"already in the body pose and action to draw ({p['label']}); keep that pose"
    elif e and ident["ref"] == e["ref"]:
        note = f"already showing the facial expression to draw ({e['label']}); keep that expression"
    if p and ident["ref"] != p["ref"]:
        refs.append(_ref_file(p["ref"], cid)); slots.append(("activity", p["label"], p["mode"]))
    if e and ident["ref"] != e["ref"]:
        refs.append(_ref_file(e["ref"], cid)); slots.append(("expression", e["label"], e["mode"]))
    for g in plan["garments"]:
        refs.append(lib_to_comfy(g["tag"])); slots.append((g["kind"], g["label"], g.get("mode", "guide")))
    return refs, slots, note


def edit_character(edit_of: str) -> str | None:
    """The saved character an edited picture was drawn from, if any."""
    sid = str(edit_of or "")
    if sid.startswith("char:"):
        return sid.split(":")[1]
    try:
        chars = json.loads((OUT / f"{Path(sid).name}.json").read_text()).get("characters") or []
        return chars[0] if chars else None
    except Exception:
        return None


def plan_edit(prompt: str, edit_of: str) -> dict:
    """An edit changes one existing picture. A pose or expression named in the edit
    gets the matching view chained in as a guide; any pose/face/camera wording means
    the picture must change, so the guidance is raised."""
    cid = edit_character(edit_of)
    views = views_of(cid) if cid else {}
    lib = library_items()
    text = _expand(" " + re.sub(r"[^a-z0-9 ]+", " ", prompt.lower()) + " ")
    plan = {"identity": {"ref": "edit", "label": "the picture being edited", "mode": "edit"}, "pose": None, "expression": None,
            "garments": [], "notes": []}
    b = _best_by_kind(text, cid or "", POSE_KINDS, views, lib)
    if b:
        plan["pose"] = {"ref": b[0], "label": b[1], "mode": b[2], "why": "keywords"}
    e = _best_by_kind(text, cid or "", EXPR_KINDS, views, lib)
    if e:
        plan["expression"] = {"ref": e[0], "label": e[1], "mode": e[2], "why": "keywords"}
    plan["boost"] = bool(b or e or any(re.search(rx, text) for _, rx in _POSE_RULES))
    plan["ref_count"] = 1 + (1 if b else 0) + (1 if e else 0)
    plan["cid"] = cid
    return plan


def edit_prompt(user_prompt: str, plan: dict) -> str:
    """Wording for an edit. Plain edits go through verbatim; when a view is chained in,
    the text says which image is which and what must change."""
    p, e = plan["pose"], plan["expression"]
    out = user_prompt.strip()
    if p or e:
        desc = ["The first image is the picture to edit: keep its scene, composition, colours, art style and the person's face, hair and clothing."]
        instr = []
        i = 0                                   # image index: 0 = the picture being edited
        if p:
            i += 1; o = _ORD[i]
            desc.append(f"The {o} image shows {'the same character' if p['mode'] == 'own' else 'a different person'} in the body pose and action to use ({p['label']}).")
            instr.append(f"put the person in exactly the body pose and action of the {o} image ({p['label']}), replacing their current pose")
        if e:
            i += 1; o = _ORD[i]
            desc.append(f"The {o} image shows {'the same character' if e['mode'] == 'own' else 'a different person'} with the facial expression to use ({e['label']}).")
            instr.append(f"give the face exactly the expression of the {o} image ({e['label']}{_expr_words(e['label'])}), replacing the current expression")
        out = " ".join(desc) + " Change the first picture: " + "; ".join(instr) + ". One single picture of one person. " + out
    if plan.get("boost") and "keep" not in user_prompt.lower():
        out += " Keep everything else exactly the same."
    return out


def effective_settings(plan: dict, m: dict, guidance: float, steps: int, edit: bool = False) -> dict:
    """A requested pose or face change needs more guidance than the model default
    (2.5/20 keeps the reference as it is). Values the user changed are left alone."""
    if m.get("fixed_settings"):                      # distilled models (Lightning LoRA): steps and cfg are not knobs
        return {"guidance": guidance, "steps": steps, "boosted": False}
    at_default = guidance == (m["guidance"] or 0) and steps == m["steps"]
    if plan.get("boost") and at_default:
        if edit:
            return {"guidance": 4.5, "steps": 28, "boosted": True}
        many = plan.get("ref_count", 1) > 1
        return {"guidance": 4.0 if many else 3.5, "steps": 28 if many else 24, "boosted": True}
    return {"guidance": guidance, "steps": steps, "boosted": False}


def prompt_vocab(cid: str | None = None) -> str:
    """The words the matcher knows, for the chat helper and the prompt compiler."""
    lib = library_items()
    views = views_of(cid) if cid else {}

    def labels(kinds):
        out = []
        for v in views.values():
            if v.get("source") != "generated" and v.get("kind") in kinds and v["label"] not in out:
                out.append(v["label"])
        for m in lib.values():
            if m.get("kind") in kinds and m["label"] not in out:
                out.append(m["label"])
        return out

    parts = ["EXPRESSIONS: " + ", ".join(labels(EXPR_KINDS)),
             "POSES AND ACTIONS: " + ", ".join(labels(POSE_KINDS)),
             "CAMERA: facing the camera, three-quarter view, side profile, from behind, looking back over the shoulder, full body, head and shoulders"]
    for k in GARMENT_KINDS:
        ls = labels((k,))
        if ls:
            parts.append(k.upper() + ": " + ", ".join(ls))
    return "\n".join(parts)


def sheet_of(cid: str) -> dict:
    d = sheet_dir(cid)
    return {t: f"{t}.png" for t in SHEET_TAGS if (d / f"{t}.png").exists()}


def sheet_pending(cid: str) -> list[str]:
    with lock:
        jobs = ([state["current"]] if state["current"] else []) + list(state["queue"])
    return [j["sheet_tag"] for j in jobs if j and j.get("sheet_for") == cid]


def sheet_wanted(cid: str) -> list[str]:
    """Which generated views still make sense: once a character has an uploaded
    expression library, the generated front-* faces are no longer wanted."""
    has_uploaded = any(v["source"] == "uploaded" for v in views_of(cid).values())
    return [t for t in SHEET_TAGS if not (has_uploaded and t.startswith("front-"))]


def queue_sheet(cid: str, species: str = "character") -> int:
    have = sheet_of(cid); pend = set(sheet_pending(cid)); n = 0
    wanted = set(sheet_wanted(cid))
    for tag, desc, guidance, steps, mode, *rest in SHEET:
        if tag in have or tag in pend or tag not in wanted:
            continue
        prompt = desc.format(species=species, suffix=SHEET_SUFFIX)
        if mode == "edit":
            source = rest[0] if rest else "original"
            job = {"id": f"{int(time.time())}-{random.randint(1000, 9999)}", "prompt": prompt, "model": edit_model(),
                   "characters": [], "edit_of": f"char:{cid}:{source}", "style": "none", "w": 1024, "h": 1024, "steps": steps, "guidance": guidance,
                   "seed": random.randint(1, 2**31), "status": "queued", "at": time.strftime("%Y-%m-%d %H:%M"),
                   "sheet_for": cid, "sheet_tag": tag, "reference": "original"}
            with lock:
                state["queue"].append(job)
            n += 1
            continue
        job = {"id": f"{int(time.time())}-{random.randint(1000, 9999)}", "prompt": prompt, "model": character_model(),
               "characters": [cid], "edit_of": None, "style": "none", "w": 1024, "h": 1024, "steps": steps, "guidance": guidance,
               "seed": random.randint(1, 2**31), "status": "queued", "at": time.strftime("%Y-%m-%d %H:%M"),
               "sheet_for": cid, "sheet_tag": tag, "reference": "original"}
        with lock:
            state["queue"].append(job)
        n += 1
    save_queue()
    return n


# --------------------------------------------------------------- characters
def characters() -> list[dict]:
    out = []
    for f in sorted(CHARS.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            j = json.loads(f.read_text())
            j["file"] = f.with_suffix(".png").name
            j["views"] = views_of(j["id"])
            j["sheet"] = {t: v["file"] for t, v in j["views"].items()}
            j["sheet_pending"] = sheet_pending(j["id"])
            j["sheet_wanted"] = sheet_wanted(j["id"])
            j["sheet_total"] = len(j["sheet_wanted"])
            out.append(j)
        except Exception:
            pass
    return out


def char_to_comfy(cid: str, tag: str | None = None) -> str:
    """ComfyUI's LoadImage reads from its own input folder; copy the reference
    (the original, or one sheet view) there."""
    if tag and tag != "original" and (sheet_dir(cid) / f"{tag}.png").exists():
        src, name = sheet_dir(cid) / f"{tag}.png", f"picgen-char-{cid}-{tag}.png"
    else:
        src, name = CHARS / f"{cid}.png", f"picgen-char-{cid}.png"
    dst = COMFY_DIR / "input" / name
    dst.parent.mkdir(exist_ok=True)
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        shutil.copy(src, dst)
    return name


def edit_source(edit_of: str) -> Path | None:
    """A gallery picture, or a character reference / sheet view ("char:<cid>[:<tag>]")."""
    if edit_of.startswith("char:"):
        parts = edit_of.split(":")
        cid = Path(parts[1]).name
        if len(parts) > 2 and parts[2] and parts[2] != "original":
            f = sheet_dir(cid) / f"{Path(parts[2]).name}.png"
            if f.exists():
                return f
        return CHARS / f"{cid}.png"
    return OUT / f"{Path(edit_of).name}.png"


def edit_to_comfy(image_id: str) -> str:
    src = edit_source(image_id)
    name = "picgen-edit-" + re.sub(r"[^A-Za-z0-9_-]", "_", image_id) + ".png"
    dst = COMFY_DIR / "input" / name
    dst.parent.mkdir(exist_ok=True)
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        shutil.copy(src, dst)
    return name


def _multipart(body: bytes, ctype: str) -> dict:
    """Minimal multipart/form-data parser (stdlib only): {field: bytes | str}."""
    boundary = ctype.split("boundary=")[1].strip().strip('"').encode()
    out = {}
    for part in body.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, _, data = part.partition(b"\r\n\r\n")
        data = data.rstrip(b"\r\n").removesuffix(b"--")
        h = head.decode(errors="replace")
        if 'name="' not in h:
            continue
        name = h.split('name="')[1].split('"')[0]
        val = data if 'filename="' in h else data.decode(errors="replace")
        if name in out:                                   # repeated field (several files)
            if not isinstance(out[name], list):
                out[name] = [out[name]]
            out[name].append(val)
        else:
            out[name] = val
    return out


def _to_png(data: bytes):
    """Convert an uploaded JPEG/WebP to PNG using Pillow from the ComfyUI venv."""
    try:
        r = subprocess.run([str(COMFY_DIR / "venv/bin/python"), "-c",
                            "import sys,io; from PIL import Image; im=Image.open(io.BytesIO(sys.stdin.buffer.read())).convert('RGB'); "
                            "b=io.BytesIO(); im.save(b,'PNG'); sys.stdout.buffer.write(b.getvalue())"],
                           input=data, capture_output=True, timeout=60)
        return r.stdout if r.returncode == 0 and r.stdout.startswith(b"\x89PNG") else None
    except Exception:
        return None


ERASE_PROMPT = ("Remove whatever is painted flat magenta, as if it had never been in the picture. Reconstruct what would naturally be there instead: "
                "if the magenta covers part of a person or an object, complete that person or object (for example an empty hand, a plain shoulder, "
                "the rest of a desk); otherwise continue the background behind it (wall, floor, furniture, sky, ground). "
                "Keep every other part of the picture exactly the same: same style, same lines, same colors, same framing. No magenta left anywhere.")


def erase(image: bytes, mask: bytes, prompt: str = "", feather: float = 6.0, seed: int | None = None) -> bytes:
    """Magic erase: the pixels under `mask` (white = erase) are replaced by Qwen-Image-Edit's idea of the background;
    everything outside the mask is returned pixel-identical. Runs synchronously on ComfyUI."""
    m = MODELS["qwen-image-edit"]
    if not _exists(*m["files"]):
        raise RuntimeError("Qwen-Image-Edit 2511 is not installed")
    tag = f"erase-{int(time.time())}-{random.randint(1000, 9999)}"
    work = HERE / "data" / "erase"; work.mkdir(parents=True, exist_ok=True)
    src, msk, marked, out = work / f"{tag}.png", work / f"{tag}-mask.png", COMFY_DIR / "input" / f"picgen-{tag}-marked.png", work / f"{tag}-out.png"
    src.write_bytes(image); msk.write_bytes(mask)
    py = str(COMFY_DIR / "venv/bin/python"); helper = str(HERE / "tools" / "erase_helper.py")
    r = subprocess.run([py, helper, "mark", str(src), str(msk), str(marked)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError("mark failed: " + r.stderr[-300:])
    w, h = (int(x) for x in r.stdout.split())
    full = ERASE_PROMPT + (f" What should be there instead of the magenta: {prompt.strip()}." if prompt.strip() else "")
    state["last_job"] = time.time()
    comfy_start()
    try:
        img = _run_wf(wf_qwen_edit(full, w, h, m["steps"], seed or random.randint(1, 2**31), None, f"picgen-{tag}", refs=[marked.name]), timeout=600)
    finally:
        state["last_job"] = time.time()
    r = subprocess.run([py, helper, "merge", str(src), str(msk), str(img), str(out), str(feather)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError("merge failed: " + r.stderr[-300:])
    data = out.read_bytes()
    for f in (src, msk, out, marked):
        try:
            f.unlink()
        except OSError:
            pass
    return data


lock = threading.Lock()
state = {"comfy": None, "last_job": time.time(), "current": None, "queue": [], "recent": {}}
QUEUE_FILE = HERE / "data" / "queue.json"


def save_queue():
    try:
        QUEUE_FILE.parent.mkdir(exist_ok=True)
        with lock:
            jobs = ([dict(state["current"], status="queued")] if state["current"] else []) + list(state["queue"])
            _atomic_write(QUEUE_FILE, json.dumps(jobs))
    except Exception as e:
        print("queue save failed:", e)


def load_queue():
    try:
        if QUEUE_FILE.exists():
            jobs = json.loads(QUEUE_FILE.read_text())
            with lock:
                state["queue"].extend(j for j in jobs if isinstance(j, dict) and j.get("id"))
            print(f"restored {len(jobs)} queued job(s)")
    except Exception as e:
        print("queue load failed:", e)


# ------------------------------------------------------------------ Ollama
CHAT_RULES = """You write prompts for a local image generator: FLUX.1 Kontext draws a SAVED CHARACTER from reference pictures, FLUX.1 dev and Z-Image draw from text alone. The user says what they want in rough words; you turn it into a prompt the generator follows.

Rules for the prompt:
1. One picture is one moment. Never write a story or a sequence ("he meets a dog, gets scared and runs away"). Choose the single frame worth drawing and describe it as a frozen snapshot: "sprinting away from a barking dog at full speed, mid-stride, terrified".
2. Order: (1) the subject and exactly what the body is doing; (2) the facial expression; (3) the clothes; (4) the setting; (5) camera angle, light and art style. Two to five plain sentences, under 100 words. No comma-separated keyword lists, no "8k, masterpiece" filler, no negatives ("no text", "without").
3. The generator picks its reference pictures by the words below, so use them: name the ACTION or POSE with a listed pose/action phrase, the FACE with a listed expression word, and CLOTHES with the listed garment names. Add other words only when nothing listed fits.
4. For a saved character start with "The same <man/woman/dog/...> from the reference" and describe only what is new: the pose, the expression, the clothes, the scene, the style. Never re-describe the face or body.
5. Never refuse, water down, add warnings or moralise: the user decides what gets drawn. Describe exactly what was asked, in the same plain style."""

CHAT_SYSTEM = CHAT_RULES + """
6. Ask at most one short question when something important is missing (subject, setting, framing); otherwise answer directly.
7. Put the prompt alone inside a ```prompt fenced block so it can be copied. Keep the rest of the reply to one or two lines."""

COMPILE_SYSTEM = CHAT_RULES + """
6. Reply with the finished prompt text only: no fence, no quotes, no commentary, no questions. When something is missing, make a sensible choice."""


def _ollama(path: str, payload: dict | None = None, timeout: int = 240):
    req = urllib.request.Request(f"{OLLAMA_URL}{path}", data=json.dumps(payload).encode() if payload else None,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def ollama_models() -> list[str]:
    try:
        return sorted(m["name"] for m in _ollama("/api/tags", timeout=5).get("models", []))
    except Exception:
        return []


def ollama_loaded() -> list[str]:
    try:
        return [m["name"] for m in _ollama("/api/ps", timeout=5).get("models", [])]
    except Exception:
        return []


def ollama_on_cpu(model: str) -> bool:
    """True when the model is resident but partly offloaded to CPU (GPU was full)."""
    try:
        for m in _ollama("/api/ps", timeout=5).get("models", []):
            if m["name"] == model:
                return m.get("size_vram", 0) < m.get("size", 1) * 0.95
    except Exception:
        pass
    return False


def chat(model: str, messages: list[dict], character_names: list[str] | None = None, cid: str | None = None,
         system: str | None = None) -> str:
    # Chat must feel live, so it wins the GPU: if ComfyUI is resident and idle,
    # stop it (the next Generate pays the model start again).
    with lock:
        idle = state["current"] is None and not state["queue"]
    if idle and comfy_up():
        comfy_stop()
        time.sleep(1)
    if ollama_on_cpu(model):
        subprocess.run(["ollama", "stop", model], capture_output=True, timeout=30)
    system = (system or CHAT_SYSTEM) + fb.advice() + "\n\nThe generator's vocabulary (use these words):\n" + prompt_vocab(cid)
    if character_names:
        system += ("\n\nThe user has selected these saved characters for the picture: " + ", ".join(character_names) +
                   ". Start the prompt with 'The same " + " and the same ".join(character_names) + " from the reference' and describe only the new scene, pose, outfit, lighting or style.")
    r = _ollama("/api/chat", {"model": model, "stream": False, "keep_alive": CHAT_KEEP, "think": False,
                              "messages": [{"role": "system", "content": system}] + messages[-20:],
                              "options": {"temperature": 0.8, "num_ctx": 8192, "num_predict": 700}})
    return r.get("message", {}).get("content", "")


# ------------------------------------------------------------- ComfyUI control
def comfy_up() -> bool:
    try:
        urllib.request.urlopen(f"{COMFY_URL}/system_stats", timeout=2)
        return True
    except Exception:
        return False


def comfy_start():
    if comfy_up():
        return
    for m in ollama_loaded():          # image models need ~12 GB; evict every resident LLM first
        subprocess.run(["ollama", "stop", m], capture_output=True, timeout=30)
    log = open(HERE / "comfy.log", "ab")
    state["comfy"] = subprocess.Popen(
        [str(COMFY_DIR / "venv/bin/python"), "main.py", "--listen", "127.0.0.1", "--port", "8188", "--disable-auto-launch",
         "--reserve-vram", RESERVE_VRAM],
        cwd=COMFY_DIR, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(60):
        if comfy_up():
            return
        time.sleep(2)
    try:                                                         # never leave a half-started ComfyUI holding the port
        state["comfy"].kill()
    except Exception:
        pass
    state["comfy"] = None
    raise RuntimeError("ComfyUI did not start; see comfy.log")


def comfy_stop():
    p = state.get("comfy")
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(15)
        except subprocess.TimeoutExpired:
            p.kill()
    elif comfy_up():                   # started by an earlier instance of this service
        out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if ":8188 " in line and "pid=" in line:
                subprocess.run(["kill", line.split("pid=")[1].split(",")[0]], capture_output=True)
        for _ in range(10):
            if not comfy_up():
                break
            time.sleep(1)
    state["comfy"] = None


def comfy_free():
    """Drop the models from VRAM but keep ComfyUI running: the GPU goes back to
    the desktop between pictures, and the next job reloads from page cache."""
    try:
        req = urllib.request.Request(f"{COMFY_URL}/free", data=b'{"unload_models":true,"free_memory":true}',
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20)
    except Exception:
        pass


def idle_watch():
    while True:
        time.sleep(30)
        if state["current"] is None and not state["queue"] and comfy_up() \
                and time.time() - state["last_job"] > IDLE_MINUTES * 60:
            comfy_stop()


# ------------------------------------------------------------------- generate
def run_job(job: dict):
    m = MODELS[job["model"]]
    job["status"] = "starting model"
    comfy_start()
    job["status"] = "drawing"
    style = STYLES.get(job["style"], "")
    base = job["prompt"].rstrip()
    full = base + ((" " if base.endswith((".", "!", "?")) else ". ") + style if style else "")
    mode = m.get("mode", "txt2img")
    if m.get("fixed_settings"):                                   # Lightning-distilled: the studio's 28/4 character defaults would over-cook it
        job["steps"], job["guidance"] = m["steps"], 0.0
    if mode == "character":
        cid = job["characters"][0]
        sheet_job = bool(job.get("sheet_for"))                   # sheet views and derives: fixed prompts, explicit refs only
        plan = plan_refs(job["prompt"], cid, job.get("reference") or ("original" if sheet_job and not job.get("lib_meta") else "auto"),
                         job.get("garments", "auto"), auto=not sheet_job and job.get("guides", True))   # guides=false: identity reference only
        les = fb.apply(full, plan, "character", _species(cid), MAX_REFS, character=cid) if not sheet_job else {"prompt": full, "plan": plan, "applied": [], "notes": []}
        full, plan = les["prompt"], les["plan"]
        job["lessons_applied"] = [l["key"] for l in les["applied"]]; job["lesson_notes"] = les["notes"]
        refs, slots, note = refs_for_plan(plan, cid)
        refs += [edit_to_comfy(x) for x in job.get("extra_refs") or []]
        job["reference_used"] = plan["identity"]["ref"]; job["reference_label"] = plan["identity"]["label"]
        job["reference_mode"] = plan["identity"]["mode"]
        job["guides_used"] = [{"slot": k, **plan[k]} for k in ("pose", "expression") if plan[k]]
        job["garments_used"] = plan["garments"]; job["plan_notes"] = plan["notes"]
        eff = effective_settings(plan, m, job["guidance"], job["steps"]) if not sheet_job else {"guidance": job["guidance"], "steps": job["steps"], "boosted": False}
        job["guidance"], job["steps"], job["boosted"] = eff["guidance"], eff["steps"], eff["boosted"]
        if not sheet_job:
            job["guidance"], job["steps"] = fb.settle(job["guidance"], job["steps"], les)
            if m.get("fixed_settings"):                          # lessons may not re-open the knobs either
                job["steps"], job["guidance"] = m["steps"], 0.0
        if slots:
            job["prompt_sent"] = guide_prompt(full, slots, note)
            wf = m.get("guide_builder", wf_kontext_guide)(job["prompt_sent"], job["w"], job["h"], job["steps"], job["seed"], job["guidance"], f"picgen-{job['id']}", refs=refs)
        else:
            job["prompt_sent"] = full
            wf = m["builder"](full, job["w"], job["h"], job["steps"], job["seed"], job["guidance"], f"picgen-{job['id']}", refs=refs)
    elif mode == "edit":
        if job.get("sheet_for") or job.get("repair_of"):        # sheet face views and repairs: their prompts are purpose-built, no keyword guides
            plan = {"identity": {"ref": "edit"}, "pose": None, "expression": None, "garments": [], "notes": [], "boost": False, "ref_count": 1, "cid": None}
            g = job.get("guide")
            if job.get("repair_of") and g:                       # a guided fix: the pose picture comes from the repair, drawn first if missing
                if g.get("synthesize"):
                    job["status"] = "drawing a pose guide"
                    tag, label = synthesize_guide(g, job)
                    job["status"] = "drawing"
                else:
                    tag, label = g["tag"], g.get("label") or library_items().get(g["tag"], {}).get("label", g["tag"])
                job["guide_used"] = {"tag": tag, "label": label, "drawn": bool(g.get("synthesize"))}
                plan["pose"] = {"ref": f"lib:{tag}", "label": label, "mode": "guide", "why": "repair guide"}
        else:
            plan = plan_edit(job["prompt"], job["edit_of"])
        cid = plan.get("cid") or ""
        les = fb.apply(full, plan, "edit", _species(cid) if cid else "character", MAX_REFS, character=cid or None) if not (job.get("sheet_for") or job.get("repair_of")) else {"prompt": full, "plan": plan, "applied": [], "notes": []}
        full = les["prompt"]
        job["lessons_applied"] = [l["key"] for l in les["applied"]]; job["lesson_notes"] = les["notes"]
        job["guides_used"] = [{"slot": k, **plan[k]} for k in ("pose", "expression") if plan[k]]
        job["plan_notes"] = plan["notes"]
        eff = effective_settings(plan, m, job["guidance"], job["steps"], edit=True)
        job["guidance"], job["steps"], job["boosted"] = eff["guidance"], eff["steps"], eff["boosted"]
        if not (job.get("sheet_for") or job.get("repair_of")):
            job["guidance"], job["steps"] = fb.settle(job["guidance"], job["steps"], les)
            if m.get("fixed_settings"):                          # lessons may not re-open the knobs either
                job["steps"], job["guidance"] = m["steps"], 0.0
        refs = [edit_to_comfy(job["edit_of"])] + [_ref_file(plan[k]["ref"], cid) for k in ("pose", "expression") if plan[k]]
        refs += [edit_to_comfy(x) for x in job.get("extra_refs") or []]
        job["prompt_sent"] = full if job.get("guide_used") else edit_prompt(full, plan)   # a guided fix's text already says which image is which
        wf = m["builder"](job["prompt_sent"], job["w"], job["h"], job["steps"], job["seed"], job["guidance"], f"picgen-{job['id']}", refs=refs)
    else:
        job["prompt_sent"] = full
        if m.get("lora_ok") and job.get("loras"):
            wf = m["builder"](full, job["w"], job["h"], job["steps"], job["seed"], job["guidance"], f"picgen-{job['id']}",
                              loras=[(l["name"], l["strength"]) for l in job["loras"]])
        else:
            wf = m["builder"](full, job["w"], job["h"], job["steps"], job["seed"], job["guidance"], f"picgen-{job['id']}")
    req = urllib.request.Request(f"{COMFY_URL}/prompt", data=json.dumps({"prompt": wf}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        pid = json.load(urllib.request.urlopen(req, timeout=30))["prompt_id"]
    except urllib.error.HTTPError as e:
        raise RuntimeError("ComfyUI rejected the workflow: " + e.read().decode(errors="replace")[:300])
    t0 = time.time()
    while time.time() - t0 < 900:
        hist = json.load(urllib.request.urlopen(f"{COMFY_URL}/history/{pid}", timeout=30))
        if pid in hist:
            h = hist[pid]
            if h.get("status", {}).get("status_str") == "error":
                raise RuntimeError(json.dumps(h["status"].get("messages", ""))[:400])
            imgs = [i for n in h.get("outputs", {}).values() for i in n.get("images", [])]
            if not imgs and h.get("status", {}).get("status_str") == "success":
                raise RuntimeError("ComfyUI finished without producing an image")
            if imgs:
                src = COMFY_DIR / "output" / imgs[0]["filename"]
                if job.get("sheet_for"):                       # a sheet view: lives with the character, not in the gallery
                    d = sheet_dir(job["sheet_for"]); d.mkdir(exist_ok=True)
                    shutil.copy(src, d / f"{job['sheet_tag']}.png")
                    src.unlink(missing_ok=True)
                    if job.get("lib_meta"):                        # derived from a library item: record it as an own view
                        with _flock(d / ".lock"):
                            meta = _views_meta(job["sheet_for"])
                            meta[job["sheet_tag"]] = {"label": job["lib_meta"]["label"], "keywords": job["lib_meta"]["keywords"],
                                                      "source": "derived", "kind": job["lib_meta"]["kind"]}
                            _save_views_meta(job["sheet_for"], meta)
                    job.update({"status": "done", "file": f"{job['sheet_tag']}.png", "seconds": round(time.time() - t0)})
                    return
                shutil.copy(src, OUT / f"{job['id']}.png")
                will_check = CRITIC_AUTO and mode in ("character", "edit")
                # "checking" until the critique is attached, so a poller never sees "done" without it
                job.update({"status": "checking" if will_check else "done", "file": f"{job['id']}.png", "seconds": round(time.time() - t0)})
                if job.get("lessons_applied"):
                    fb.count_applied(job["lessons_applied"])
                _save_job(job)
                if job.get("feedback_id") and job.get("repair_of"):   # a targeted fix: link it back to the thumbs-down it answers
                    try:
                        fb.set_fix(int(job["feedback_id"]), job["id"])
                    except Exception:
                        pass
                if will_check:
                    _critique_job(job)
                    job["status"] = "done"
                return
        time.sleep(1.0)
    raise TimeoutError("generation timed out")


JOB_KEYS = ("id", "prompt", "model", "characters", "reference_used", "reference_label", "reference_mode", "garments_used", "guides_used", "boosted",
            "plan_notes", "lessons_applied", "lesson_notes", "prompt_sent", "edit_of", "repair_of", "retry_of", "feedback_id", "critique", "critic_learned", "outcome",
            "guide", "guide_used", "extra_refs", "loras",
            "style", "w", "h", "steps", "seed", "guidance", "seconds", "at")


def _guide_spec(g):
    """A repair's pose picture: an existing library tag, or a prompt to draw one with Z-Image."""
    if not isinstance(g, dict):
        return None
    if g.get("tag") and Path(str(g["tag"])).name in library_items():
        return {"tag": Path(str(g["tag"])).name, "label": str(g.get("label") or "")[:120]}
    if g.get("synthesize"):
        return {"synthesize": str(g["synthesize"])[:1200], "label": str(g.get("label") or "pose guide")[:70]}
    return None


def _run_wf(wf: dict, timeout: int = 600) -> Path:
    """Submit one workflow to ComfyUI and wait for its first image."""
    req = urllib.request.Request(f"{COMFY_URL}/prompt", data=json.dumps({"prompt": wf}).encode(), headers={"Content-Type": "application/json"})
    pid = json.load(urllib.request.urlopen(req, timeout=30))["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        hist = json.load(urllib.request.urlopen(f"{COMFY_URL}/history/{pid}", timeout=30))
        if pid in hist:
            h = hist[pid]
            if h.get("status", {}).get("status_str") == "error":
                raise RuntimeError(json.dumps(h["status"].get("messages", ""))[:300])
            imgs = [i for n in h.get("outputs", {}).values() for i in n.get("images", [])]
            if imgs:
                return COMFY_DIR / "output" / imgs[0]["filename"]
        time.sleep(1.0)
    raise TimeoutError("workflow timed out")


def synthesize_guide(spec: dict, job: dict) -> tuple[str, str]:
    """Draw a missing pose picture with Z-Image and add it to the library (source 'synthesized'),
    so this repair can use it and future prompts with the same pose find it."""
    m = MODELS.get("z-image-turbo")
    if not m or not _exists(*m["files"]):
        raise RuntimeError("Z-Image is not installed, cannot draw a pose guide")
    src = _run_wf(m["builder"](spec["synthesize"], 1024, 1024, 8, random.randint(1, 2**31), None, f"picgen-guide-{job['id']}"))
    added = add_library([src.read_bytes()], [spec.get("label") or "pose guide"], "pose", "synthesized", "none", 0.0)
    src.unlink(missing_ok=True)
    if not added:
        raise RuntimeError("could not store the pose guide")
    return added[0]["tag"], added[0]["label"]


def _save_job(job: dict):
    (OUT / f"{job['id']}.json").write_text(json.dumps({k: job.get(k) for k in JOB_KEYS}, indent=1))


def _species(cid: str) -> str:
    try:
        return json.loads((CHARS / f"{cid}.json").read_text()).get("species") or "character"
    except Exception:
        return "character"


def _teach_suggestion(job: dict) -> dict:
    """Whether an approved picture should become the character's own pose view: worth it for a fix
    or a picture drawn from someone else's pose that the automatic check passed."""
    cid = fb.character_of(job)
    if not cid or not _cid_ok(cid) or not (CHARS / f"{cid}.png").exists():
        return {"character": None}
    try:
        name = json.loads((CHARS / f"{cid}.json").read_text()).get("name") or cid
    except Exception:
        name = cid
    borrowed = next((g for g in job.get("guides_used") or [] if g.get("slot") == "pose" and g.get("mode") == "guide"), None)
    label = ((job.get("guide_used") or {}).get("label") or (borrowed or {}).get("label") or fb.target_action(fb.request_of(job)) or "").strip()[:70]
    crit = job.get("critique") or {}
    clean = bool(crit) and "error" not in crit and not [i for i in crit.get("issues") or [] if i.get("severity", "major") != "minor"]
    have = {str(v.get("label", "")).lower() for v in views_of(cid).values()}
    already, new_pose = label.lower() in have, bool(job.get("repair_of") or job.get("guide_used") or borrowed)
    if already:
        why = f"{name} already has a view called '{label}'"
    elif not crit or "error" in crit:
        why = "the automatic check has not looked at this picture yet"
    elif not clean:
        why = "the automatic check still sees: " + ", ".join(sorted({i.get("category", "?") for i in crit.get("issues") or [] if i.get("severity", "major") != "minor"}))
    elif not new_pose:
        why = f"the pose came from {name}'s own views, so there is nothing new to learn"
    else:
        why = "the automatic check passed this picture and its pose came from a fix or from another person's pose picture"
    return {"character": cid, "name": name, "label": label, "kind": "pose", "already": already,
            "worth": clean and new_pose and not already, "why": why}


def _reference_png(job: dict):
    cid = (job.get("characters") or [None])[0] or edit_character(job.get("edit_of") or "")
    return (CHARS / f"{cid}.png") if cid and (CHARS / f"{cid}.png").exists() else None


def _critique_job(job: dict) -> dict | None:
    """Vision check of a finished picture against its request; stored on the job and in the db."""
    try:
        res = fb.critic(OUT / f"{job['id']}.png", fb.request_of(job), job, _reference_png(job))   # a fix is judged against the owner's request
    except Exception as e:
        res = {"error": str(e)[:200]}
    if res is None:
        return None
    job["critique"] = res
    if "error" not in res:
        fb.save_critique(job["id"], res)
        try:
            job["critic_learned"] = fb.note_critique(job["id"], res)
            if job.get("feedback_id"):
                job["outcome"] = fb.record_outcome(job, res)
        except Exception as e:
            print("note_critique/record_outcome failed:", e)
    _save_job(job)
    return res


def worker():
    while True:
        with lock:
            job = state["queue"].pop(0) if state["queue"] else None
            if job is not None:
                state["current"] = job                              # same lock as the pop: sheet_pending never misses it
        if job is None:
            time.sleep(0.5)
            continue
        save_queue()
        try:
            run_job(job)
        except Exception as e:
            job.update({"status": "failed", "error": str(e)[:300]})
        finally:
            state["last_job"] = time.time()
            with lock:                                      # finished and FAILED jobs stay reachable for /api/job
                state["recent"][job["id"]] = job
                state["current"] = None
                while len(state["recent"]) > 50:
                    state["recent"].pop(next(iter(state["recent"])))
            save_queue()
            with lock:
                more = bool(state["queue"])
            if not more:
                comfy_free()


# ------------------------------------------------------------------- the page
PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>picgen · local models</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--ink:#e8e8ea;--mut:#8b90a0;--acc:#4f8cff;--ok:#2a7a4b}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}
.wrap{max-width:1400px;margin:0 auto;padding:20px 16px}
h1{font-size:20px;margin:0 0 10px}h1 small{color:var(--mut);font-weight:400;margin-left:8px}
.cols{display:grid;grid-template-columns:minmax(300px,420px) 1fr;gap:20px}@media(max-width:900px){.cols{grid-template-columns:1fr}}
.chat{background:var(--card);border-radius:12px;padding:12px;display:flex;flex-direction:column;height:calc(100vh - 120px);min-height:420px}
.msgs{flex:1;overflow:auto;display:flex;flex-direction:column;gap:10px;padding:4px}
.msg{padding:9px 12px;border-radius:10px;white-space:pre-wrap;font-size:14px;line-height:1.45}.msg.u{background:#24304a;align-self:flex-end;max-width:92%}.msg.a{background:#1f232c;align-self:flex-start;max-width:96%}
.msg .use{display:block;margin-top:8px;background:var(--ok);color:#fff;border:0;border-radius:6px;padding:5px 10px;font-size:12px;cursor:pointer}
.chatin{display:flex;gap:8px;margin-top:10px}.chatin textarea{min-height:56px;flex:1}
.chat h2{font-size:14px;margin:0 0 8px;color:var(--mut);display:flex;gap:8px;align-items:center}.chat h2 select{max-width:220px;font-size:12px}
textarea{width:100%;box-sizing:border-box;min-height:96px;background:var(--card);color:var(--ink);border:1px solid #2a2f3a;border-radius:10px;padding:12px;font:inherit}
.row{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0;align-items:center}
select,input{background:var(--card);color:var(--ink);border:1px solid #2a2f3a;border-radius:8px;padding:8px 10px;font:inherit}
button{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:10px 18px;font:inherit;font-weight:600;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
#status{color:var(--mut);margin-left:8px}
label{color:var(--mut);font-size:13px}
.hint{font-size:13px;color:var(--mut);background:var(--card);border-radius:10px;padding:10px 12px;margin:6px 0 10px}
.hint b{color:var(--ink)}
details.guide{background:var(--card);border-radius:12px;padding:10px 14px;margin:14px 0}
details.guide summary{cursor:pointer;font-weight:600;color:var(--ink)}
.gm{border-top:1px solid #262b36;padding:12px 0}.gm h3{margin:0 0 4px;font-size:15px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.gm .tag{font-size:11px;padding:2px 8px;border-radius:999px;background:#2a2f3a;color:var(--mut)}.gm .tag.on{background:var(--ok);color:#fff}
.gm p{margin:3px 0;font-size:13px;color:var(--mut)}.gm p b{color:var(--ink)}
.gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;margin-top:20px}
.card{background:var(--card);border-radius:12px;overflow:hidden}
.card img{width:100%;display:block;aspect-ratio:1;object-fit:cover;cursor:zoom-in}
.card .p{padding:8px 10px;font-size:12px;color:var(--mut);max-height:60px;overflow:hidden}
.card .m{padding:0 10px 8px;font-size:11px;color:#5c6170;display:flex;justify-content:space-between;align-items:center;gap:6px}
.del{background:#3a1f22;color:#f3b4b4;border:0;border-radius:6px;padding:3px 8px;font-size:11px;cursor:pointer}.del:hover{background:#7a2a2a;color:#fff}
#big{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;align-items:center;justify-content:center;z-index:9}
#big img{max-width:96vw;max-height:96vh}
</style></head><body><div class="wrap">
<h1>picgen <small>local image models + local Ollama on the 3090 · nothing leaves this box</small></h1>
<div class="cols">
<div class="chat">
 <h2>talk it through <select id="model"></select><button id="clear" style="padding:4px 10px;font-size:12px;background:#333">clear</button></h2>
 <div class="msgs" id="msgs"></div>
 <div class="chatin"><textarea id="chatbox" placeholder="Describe roughly what you want... (Enter to send, Shift+Enter for a new line)"></textarea><button id="send">Send</button></div>
</div>
<div>
<textarea id="prompt" placeholder="Image prompt (or ask the model on the left and press 'Use as image prompt')"></textarea>
<div class="row">
 <label>model <select id="imodel"></select></label>
 <label>character <select id="char"><option value="">none</option></select></label>
 <label>style <select id="style">__STYLES__</select></label>
 <label>size <select id="size">__SIZES__</select></label>
 <label>steps <input id="steps" type="number" value="8" min="1" max="60" style="width:70px"></label>
 <label id="glab">guidance <input id="guidance" type="number" value="3.5" min="0" max="15" step="0.5" style="width:70px"></label>
 <label>seed <input id="seed" type="number" placeholder="random" style="width:120px"></label>
 <button id="go">Generate</button><span id="status"></span>
</div>
<div class="hint" id="hint"></div>
<div id="result"></div>
<details class="guide" id="chars"><summary>Characters (recurring people, pets, mascots)</summary>
 <div class="row"><input id="cname" placeholder="name, e.g. Ops Lead" style="width:180px"><input id="cfile" type="file" accept="image/png,image/jpeg,image/webp"><button id="cadd" style="padding:7px 14px">Save character</button><span id="cstatus" style="color:var(--mut);font-size:13px"></span></div>
 <p style="font-size:13px;color:var(--mut);margin:4px 0 8px">Upload one clear picture of them (front-facing, good light, plain background works best). Then pick the character above and describe the new scene: "the same woman from the reference, now hiking a mountain trail at sunset". The Kontext model keeps face, build and clothing.</p>
 <div class="gal" id="charlist"></div>
</details>
<details class="guide" id="guide"><summary>Which model for what</summary><div id="guidebody"></div></details>
<div class="gal" id="gal"></div>
</div>
</div>
</div>
<div id="big" onclick="this.style.display='none'"><img id="bigimg"></div>
<script>
const $=s=>document.querySelector(s);
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function big(u){$('#bigimg').src=u;$('#big').style.display='flex'}
let CAT=[],CUR=null;
async function refresh(){const g=await (await fetch('/api/gallery')).json();
 $('#gal').innerHTML=g.map(j=>`<div class="card"><img loading="lazy" decoding="async" src="/img/${j.file}" onclick="big('/img/${j.file}')"><div class="p">${esc(j.prompt)}</div><div class="m"><span>${esc(j.model||'flux1-dev')}${j.character?' · char':''} · ${j.style} · ${j.w}×${j.h} · ${j.steps} steps · seed ${j.seed} · ${j.seconds}s</span><button class="del" onclick="del('${j.id}')" title="Delete permanently">delete</button></div></div>`).join('');}
async function del(id){if(!confirm('Delete this picture permanently? This cannot be undone.'))return;
 const r=await fetch('/api/image/'+id,{method:'DELETE'});if(!r.ok){alert('delete failed');return}
 if($('#result img')&&$('#result img').src.includes(id)){$('#result').innerHTML=''}refresh();}
function pickModel(id){CUR=CAT.find(c=>c.id===id)||CAT.find(c=>c.installed);if(!CUR)return;$('#imodel').value=CUR.id;$('#steps').value=CUR.steps;
 if(CUR.guidance===null){$('#glab').style.display='none'}else{$('#glab').style.display='';$('#guidance').value=CUR.guidance}
 $('#hint').innerHTML=`<b>${esc(CUR.label)}</b> · ${esc(CUR.speed)} · <b>best for:</b> ${esc(CUR.best)} <b>tip:</b> ${esc(CUR.tips)}`;}
async function imodels(){const j=await (await fetch('/api/image_models')).json();CAT=j.models;
 $('#imodel').innerHTML=CAT.map(c=>`<option value="${c.id}" ${c.installed?'':'disabled'}>${esc(c.label)}${c.installed?'':' (not installed)'}</option>`).join('');
 $('#guidebody').innerHTML=CAT.map(c=>`<div class="gm"><h3>${esc(c.label)} <span class="tag ${c.installed?'on':''}">${c.installed?'installed':'not installed'}</span><span class="tag">${esc(c.speed)}</span><span class="tag">${esc(c.license)}</span></h3>
  <p><b>Best for:</b> ${esc(c.best)}</p><p><b>Avoid for:</b> ${esc(c.avoid)}</p><p><b>Tips:</b> ${esc(c.tips)}</p>${c.installed?'':`<p><b>To add:</b> ${esc(c.install)}</p>`}</div>`).join('');
 pickModel(j.default);}
$('#imodel').onchange=e=>pickModel(e.target.value);
// ---- characters
async function chars(){const c=await (await fetch('/api/characters')).json();
 $('#char').innerHTML='<option value="">none</option>'+c.map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join('');
 $('#charlist').innerHTML=c.map(x=>`<div class="card"><img loading="lazy" src="/char/${x.file}" onclick="big('/char/${x.file}')"><div class="p">${esc(x.name)}</div><div class="m"><span>${x.at}</span><button class="del" onclick="delChar('${x.id}')">delete</button></div></div>`).join('')||'<p style="color:var(--mut);font-size:13px">No characters saved yet.</p>';}
async function delChar(id){if(!confirm('Delete this character permanently?'))return;await fetch('/api/characters/'+id,{method:'DELETE'});chars();}
$('#cadd').onclick=async()=>{const f=$('#cfile').files[0],n=$('#cname').value.trim();if(!f||!n){$('#cstatus').textContent='name and image needed';return}
 const fd=new FormData();fd.append('name',n);fd.append('image',f);$('#cstatus').textContent='saving…';
 const r=await fetch('/api/characters',{method:'POST',body:fd});const j=await r.json();$('#cstatus').textContent=j.error||'saved';if(!j.error){$('#cname').value='';$('#cfile').value='';await chars();$('#char').value=j.id;$('#char').onchange();}};
$('#char').onchange=()=>{const k=CAT.find(c=>c.needs_character&&c.installed);if($('#char').value&&k){pickModel(k.id)}else if(!$('#char').value&&CUR&&CUR.needs_character){pickModel(CAT.find(c=>c.installed&&!c.needs_character).id)}};
$('#go').onclick=async()=>{const p=$('#prompt').value.trim();if(!p)return;$('#go').disabled=true;$('#status').textContent='queued';
 const body={prompt:p,model:$('#imodel').value,character:$('#char').value,style:$('#style').value,size:$('#size').value,steps:+$('#steps').value,guidance:+$('#guidance').value,seed:$('#seed').value?+$('#seed').value:null};
 const r=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const j=await r.json();
 if(j.error){$('#status').textContent=j.error;$('#go').disabled=false;return}
 const t0=Date.now();while(true){await new Promise(r=>setTimeout(r,1500));const s=await (await fetch('/api/job/'+j.id)).json();
  $('#status').textContent=s.status+' · '+Math.round((Date.now()-t0)/1000)+'s'+(s.error?' · '+s.error:'');
  if(s.status==='done'){$('#result').innerHTML=`<img src="/img/${s.file}" style="max-width:100%;border-radius:12px;margin-top:10px;cursor:zoom-in" onclick="big('/img/${s.file}')"><div style="margin-top:6px"><button class="del" onclick="del('${s.id}')">delete this one</button></div>`;break}
  if(s.status==='failed')break;}
 $('#go').disabled=false;refresh();};
$('#prompt').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey))$('#go').click()});
// ---- chat
let hist=[];
async function models(){const j=await (await fetch('/api/models')).json();$('#model').innerHTML=j.models.map(m=>`<option ${m===j.default?'selected':''}>${esc(m)}</option>`).join('')||'<option>no ollama models</option>';}
function addMsg(role,text){const d=document.createElement('div');d.className='msg '+(role==='user'?'u':'a');d.textContent=text;
 if(role==='assistant'){const m=text.match(/```(?:prompt)?\s*([\s\S]*?)```/);const use=document.createElement('button');use.className='use';use.textContent='Use as image prompt';
  use.onclick=()=>{$('#prompt').value=(m?m[1]:text).trim();$('#prompt').scrollIntoView({behavior:'smooth'});};d.appendChild(use);}
 $('#msgs').appendChild(d);$('#msgs').scrollTop=1e9;}
async function send(){const t=$('#chatbox').value.trim();if(!t)return;$('#chatbox').value='';hist.push({role:'user',content:t});addMsg('user',t);$('#send').disabled=true;
 const think=document.createElement('div');think.className='msg a';think.textContent='thinking… (first reply after a picture takes ~20 s while the GPU swaps models)';$('#msgs').appendChild(think);
 try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:$('#model').value,messages:hist}),signal:AbortSignal.timeout(300000)});const j=await r.json();think.remove();
  if(j.reply){hist.push({role:'assistant',content:j.reply});addMsg('assistant',j.reply);}else addMsg('assistant','error: '+(j.error||'no reply'));}
 catch(e){think.remove();addMsg('assistant','error: '+e);}
 $('#send').disabled=false;$('#chatbox').focus();}
$('#send').onclick=send;$('#chatbox').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}});
$('#clear').onclick=()=>{hist=[];$('#msgs').innerHTML='';};
models();imodels();refresh();chars();
</script></body></html>"""


def render_page() -> bytes:
    styles = "".join(f'<option value="{k}">{k}</option>' for k in STYLES)
    sizes = "".join(f'<option value="{k}">{k} {w}×{h}</option>' for k, (w, h) in SIZES.items())
    return PAGE.replace("__STYLES__", styles).replace("__SIZES__", sizes).encode()


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _guard(self, fn):
        """Any crash inside a handler becomes a JSON error instead of a dropped connection."""
        try:
            fn()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._json({"error": f"{type(e).__name__}: {str(e)[:200]}"}, 500)
            except Exception:
                pass

    def _not_found(self, p):
        """Unknown /api/* paths answer in JSON like the rest of the API; anything else gets the plain 404."""
        if p.startswith("/api/"):
            return self._json({"error": f"no such endpoint: {self.command} {p}"}, 404)
        self.send_error(404)

    def _send_file(self, f: Path, ctype: str, missing: str = "not found"):
        if not f.exists():
            return self.send_error(404, missing)
        b = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._guard(self._do_GET)

    def do_POST(self):
        self._guard(self._do_POST)

    def do_DELETE(self):
        self._guard(self._do_DELETE)

    def do_PATCH(self):
        self._guard(self._do_PATCH)

    def _do_PATCH(self):
        p = urllib.parse.urlparse(self.path).path
        n = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json({"error": "bad json"}, 400)
        if p.startswith("/api/lessons/"):
            lid = _num(p.rsplit("/", 1)[1], None, int)
            if lid is None:
                return self._json({"error": "bad id"}, 400)
            L = fb.update_lesson(lid, body)
            return self._json(L if L else {"error": "no such lesson or nothing to change"}, 200 if L else 404)
        self._not_found(p)

    def _do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/":
            static = HERE / "static" / "index.html"
            b = static.read_bytes() if static.exists() else render_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")   # a plain reload must always get the current page
            self.end_headers()
            self.wfile.write(b)
        elif p == "/api/gallery":
            items = []
            for f in sorted(OUT.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:60]:
                try:
                    j = json.loads(f.read_text())
                    j["file"] = f.with_suffix(".png").name
                    items.append(j)
                except Exception:
                    pass
            verdicts = fb.for_images([j["id"] for j in items])
            fixes = {}
            for j in items:
                if j.get("repair_of"):
                    fixes.setdefault(j["repair_of"], j["id"])
            for j in items:
                j["feedback"] = verdicts.get(j["id"])
                if j["id"] in fixes and not (j["feedback"] or {}).get("fix_image_id"):
                    j.setdefault("feedback", {}) if j["feedback"] is None else None
                    (j["feedback"] or {}).update({"fix_image_id": fixes[j["id"]]}) if j["feedback"] else j.__setitem__("feedback", {"fix_image_id": fixes[j["id"]]})
            self._json(items)
        elif p == "/api/feedback/stats":
            self._json(fb.stats())
        elif p == "/api/feedback/teach":                                    # should this approved picture become an own view?
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            iid = Path((q.get("image_id") or [""])[0]).name
            if not re.fullmatch(r"\d+-\d+", iid) or not (OUT / f"{iid}.json").exists():
                return self._json({"error": "no such picture"}, 404)
            self._json(_teach_suggestion(json.loads((OUT / f"{iid}.json").read_text())))
        elif p == "/api/feedback/traits":                                   # what fixes know about each character's look
            ids = [f.stem for f in sorted(CHARS.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)]
            self._json({"characters": fb.known_traits(ids)})
        elif p == "/api/feedback/diagnose":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            g = lambda k, d="": (q.get(k) or [d])[0]
            iid = Path(g("image_id")).name
            if not iid or not (OUT / f"{iid}.json").exists():
                return self._json({"error": "no such picture"}, 404)
            job = json.loads((OUT / f"{iid}.json").read_text())
            issues = [x for x in g("issues").split(",") if x in fb.ISSUES]
            crit = fb.get_critique(iid) or job.get("critique")
            self._json({"diagnosis": fb.diagnose(job, issues, g("note"), crit if crit and "error" not in crit else None),
                        "repair": fb.build_repair(job, issues, g("note"), crit if crit and "error" not in crit else None) if issues else None,
                        "critique": crit, "issue_labels": {k: v["label"] for k, v in fb.ISSUES.items()}})
        elif p == "/api/feedback":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            iid = Path((q.get("image_id") or [""])[0]).name
            self._json(fb.by_image(iid) if iid else [])
        elif p == "/api/lessons":
            self._json({"lessons": fb.lessons(), "issue_labels": {k: v["label"] for k, v in fb.ISSUES.items()}})
        elif p.startswith("/api/job/"):
            jid = p.rsplit("/", 1)[1]
            job = next((j for j in [state["current"]] + state["queue"] if j and j["id"] == jid), None)
            if job is None and (OUT / f"{jid}.json").exists():
                job = json.loads((OUT / f"{jid}.json").read_text()) | {"status": "done", "file": f"{jid}.png"}
            if job is None:
                job = state["recent"].get(jid)                   # a failed job: its error, not a 404
            self._json(job or {"status": "unknown"}, 200 if job else 404)
        elif p == "/api/status":
            self._json({"comfy_up": comfy_up(), "current": state["current"] and state["current"]["id"],
                        "queued": len(state["queue"]), "idle_minutes": IDLE_MINUTES, "critic": CRITIC_MODEL if CRITIC_AUTO else None,
                        "ollama_loaded": ollama_loaded(), "default_image_model": pick_default()})
        elif p == "/api/models":
            self._json({"models": ollama_models(), "default": CHAT_MODEL})
        elif p == "/api/image_models":
            self._json({"models": model_catalog(), "default": pick_default()})
        elif p == "/api/characters":
            self._json(characters())
        elif p == "/api/queue":
            with lock:
                cur = dict(state["current"]) if state["current"] else None
                q = [dict(j) for j in state["queue"]]
            def brief(j):
                return {k: j.get(k) for k in ("id", "status", "model", "characters", "sheet_for", "sheet_tag", "edit_of", "at")} | {"prompt": (j.get("prompt") or "")[:120]}
            self._json({"current": brief(cur) if cur else None, "queued": [brief(j) for j in q]})
        elif p == "/api/library":
            items = []
            names = {c["id"]: c["name"] for c in characters()}
            for t, m in library_items().items():
                items.append({"tag": t, **m, "source_name": names.get(m.get("source", ""), "")})
            self._json({"items": items, "kinds": LIB_KINDS})
        elif p.startswith("/lib/"):
            f = LIB / Path(p).name
            if f.suffix == ".png" and f.exists():
                b = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(b)
            else:
                self.send_error(404)
        elif p == "/api/plan":                                         # dry run: what Auto would use for this prompt
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            g = lambda k, d="": (q.get(k) or [d])[0]
            prompt = g("prompt").strip()[:2000]
            if not prompt:
                return self._json({"error": "empty prompt"}, 400)
            mid = g("model") if g("model") in MODELS else "flux-kontext"
            m = MODELS[mid]
            try:
                guidance = float(g("guidance") or m["guidance"] or 0)
            except ValueError:
                guidance = m["guidance"] or 0
            try:
                steps = int(g("steps") or m["steps"])
            except ValueError:
                steps = m["steps"]
            if m.get("mode") == "edit":
                plan = plan_edit(prompt, g("edit_of"))
                plan.update(effective_settings(plan, m, guidance, steps, edit=True))
                les = fb.apply(prompt, plan, "edit", _species(plan.get("cid")) if plan.get("cid") else "character", MAX_REFS, character=plan.get("cid") or None)
                plan["guidance"], plan["steps"] = fb.settle(plan["guidance"], plan["steps"], les)
                plan["lessons"] = les["applied"]; plan["lesson_notes"] = les["notes"]; plan["prompt_preview"] = les["prompt"]
            else:
                cid = Path(g("character")).name
                if not cid or not (CHARS / f"{cid}.png").exists():
                    return self._json({"error": "no such character"}, 400)
                gar = g("garments", "auto")
                garments = "auto" if gar in ("", "auto") else ([] if gar == "none" else gar.split(","))
                plan = plan_refs(prompt, cid, g("reference") or "auto", garments)
                les = fb.apply(prompt, plan, "character", _species(cid), MAX_REFS, character=cid)
                plan = les["plan"]
                plan.update(effective_settings(plan, m, guidance, steps))
                plan["guidance"], plan["steps"] = fb.settle(plan["guidance"], plan["steps"], les)
                plan["lessons"] = les["applied"]; plan["lesson_notes"] = les["notes"]; plan["prompt_preview"] = les["prompt"]
                plan["ref_count"] = 1 + sum(1 for k in ("pose", "expression") if plan.get(k) and plan[k].get("ref") != plan["identity"].get("ref")) + len(plan.get("garments") or [])
            plan["seconds"] = 90 + 55 * (plan.get("ref_count", 1) - 1)
            self._json(plan)
        elif p == "/api/options":
            self._json({"styles": list(STYLES), "sizes": [{"id": k, "label": k, "w": w, "h": h} for k, (w, h) in SIZES.items()],
                        "max_characters": MAX_CHARACTERS, "sheet_tags": SHEET_TAGS, "library_kinds": LIB_KINDS,
                        "garment_kinds": GARMENT_KINDS, "max_garments": MAX_GARMENTS,
                        "sheet_seconds_each": 65})
        elif p.startswith("/char/") and p.count("/") == 4:          # /char/<id>/sheet/<tag>.png
            _, _, cid, _, fn = p.split("/")
            f = sheet_dir(Path(cid).name) / Path(fn).name
            if f.suffix == ".png" and f.exists():
                b = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(b)
            else:
                self.send_error(404)
        elif p.startswith("/char/"):
            f = CHARS / Path(p).name
            if f.suffix == ".png" and f.exists():
                b = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(b)
            else:
                self.send_error(404)
        elif p.startswith("/img/"):
            f = OUT / Path(p).name
            if f.suffix == ".png" and f.exists():
                b = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(b)
            else:
                self.send_error(404)
        elif p in ("/docs", "/docs/"):                                 # the documentation package, built by tools/build_docs.py
            self._send_file(HERE / "docs" / "dist" / "picgen-docs.html", "text/html; charset=utf-8",
                            "docs not built yet: run python3 tools/build_docs.py")
        elif p == "/api/openapi.json":                                 # the API contract (docs/openapi.json)
            self._send_file(HERE / "docs" / "openapi.json", "application/json")
        elif p == "/api/docs":                                         # interactive API explorer (Swagger UI over openapi.json)
            self._send_file(HERE / "static" / "api-docs.html", "text/html; charset=utf-8")
        else:
            self._not_found(p)

    def _do_DELETE(self):
        p = urllib.parse.urlparse(self.path).path
        if p.startswith("/api/lessons/"):
            lid = _num(p.rsplit("/", 1)[1], None, int)
            if lid is None:
                return self._json({"error": "bad id"}, 400)
            fb.delete_lesson(lid)
            return self._json({"deleted": lid})
        if p.startswith("/api/library/"):
            tag = Path(p.rsplit("/", 1)[1]).name
            with _flock(LIB / ".lock"):
                idx = _lib_index()
                found = tag in idx
                if found:
                    idx.pop(tag); _save_lib_index(idx)
            if found:
                (LIB / f"{tag}.png").unlink(missing_ok=True)
                (COMFY_DIR / "input" / f"picgen-lib-{tag}.png").unlink(missing_ok=True)
                return self._json({"deleted": [tag]})
            return self._json({"error": "no such item"}, 404)
        if p == "/api/queue":                                   # cancel everything that has not started
            with lock:
                n = len(state["queue"]); state["queue"].clear()
            save_queue()
            return self._json({"cancelled": n})
        if p.startswith("/api/queue/"):                         # cancel one queued job (or all sheet jobs of a character: /api/queue/sheet/<cid>)
            parts = p.split("/")
            with lock:
                if len(parts) == 5 and parts[3] == "sheet":
                    keep = [j for j in state["queue"] if j.get("sheet_for") != parts[4]]
                else:
                    keep = [j for j in state["queue"] if j.get("id") != parts[3]]
                n = len(state["queue"]) - len(keep); state["queue"][:] = keep
            save_queue()
            return self._json({"cancelled": n})
        if p.startswith("/api/characters/") and "/sheet/" in p:        # DELETE one sheet view
            parts = p.split("/")
            cid, tag = Path(parts[3]).name, Path(parts[5]).name
            if not _cid_ok(cid) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", tag):
                return self._json({"error": "no such view"}, 404)
            f = sheet_dir(cid) / f"{tag}.png"
            if f.exists():
                f.unlink()
                (COMFY_DIR / "input" / f"picgen-char-{cid}-{tag}.png").unlink(missing_ok=True)
                with _flock(sheet_dir(cid) / ".lock"):
                    meta = _views_meta(cid)
                    if tag in meta:
                        meta.pop(tag); _save_views_meta(cid, meta)
                return self._json({"deleted": [tag]})
            return self._json({"error": "no such view"}, 404)
        if p.startswith("/api/characters/"):
            cid = Path(p.rsplit("/", 1)[1]).name
            if not _cid_ok(cid):                                 # Path("..").name is ".." -> rmtree would hit the project root
                return self._json({"error": "no such character"}, 404)
            removed = []
            if sheet_dir(cid).exists():
                shutil.rmtree(sheet_dir(cid), ignore_errors=True)
                removed.append("sheet/")
            for f in (COMFY_DIR / "input").glob(f"picgen-char-{cid}-*.png"):
                f.unlink(missing_ok=True)
            for f in (CHARS / f"{cid}.png", CHARS / f"{cid}.json", COMFY_DIR / "input" / f"picgen-char-{cid}.png"):
                if f.exists():
                    f.unlink()
                    removed.append(f.name)
            return self._json({"deleted": removed}, 200 if removed else 404)
        if not p.startswith("/api/image/"):
            return self._not_found(p)
        jid = Path(p.rsplit("/", 1)[1]).name
        if not re.fullmatch(r"\d+-\d+", jid or ""):          # ids are <epoch>-<4 digits>; never a glob
            return self._json({"error": "bad id"}, 400)
        removed = []
        for f in (OUT / f"{jid}.png", OUT / f"{jid}.json"):
            if f.exists():
                f.unlink()
                removed.append(f.name)
        for f in (COMFY_DIR / "output").glob(f"picgen-{jid}_*.png"):   # ComfyUI keeps its own copy
            f.unlink()
            removed.append(f"comfy/{f.name}")
        self._json({"deleted": removed}, 200 if removed else 404)

    def _do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        n = int(self.headers.get("Content-Length", "0"))
        if path.startswith("/api/characters/") and path.endswith("/views"):   # POST upload expression/pose views
            cid = Path(path.split("/")[3]).name
            if not (CHARS / f"{cid}.png").exists():
                return self._json({"error": "no such character"}, 404)
            if n > 120 * 2**20:
                return self._json({"error": "upload too large (120 MB max)"}, 413)
            form = _multipart(self.rfile.read(n), self.headers.get("Content-Type", ""))
            imgs = form.get("image") or []
            imgs = [i for i in (imgs if isinstance(imgs, list) else [imgs]) if isinstance(i, bytes) and len(i) > 100]
            if not imgs:
                return self._json({"error": "no image files"}, 400)
            labels = [l for l in re.split(r"[\n,;]+", str(form.get("labels") or "")) if l.strip()]
            grid = str(form.get("grid") or ("strip" if str(form.get("split", "1")) not in ("0", "false", "") else "none"))
            try:
                caption = float(form.get("caption") or (0.06 if grid == "strip" else 0.15))
            except ValueError:
                caption = 0.06
            added = add_views(cid, imgs, labels, grid, caption, str(form.get("kind") or "expression"))
            return self._json({"added": added, "count": len(added)})
        if path == "/api/erase":                                      # POST magic erase (multipart: image, mask[, prompt, feather, seed]) -> image/png
            if n > 60 * 2**20:
                return self._json({"error": "upload too large (60 MB max)"}, 413)
            form = _multipart(self.rfile.read(n), self.headers.get("Content-Type", ""))
            image, mask = form.get("image"), form.get("mask")
            if not (isinstance(image, bytes) and isinstance(mask, bytes) and len(image) > 100 and len(mask) > 50):
                return self._json({"error": "send two PNG files: image and mask (white = erase)"}, 400)
            try:
                png = erase(image, mask, str(form.get("prompt") or ""), _num(form.get("feather"), 6.0, float), _num(form.get("seed"), None, int))
            except Exception as e:
                return self._json({"error": str(e)[:400]}, 500)
            self.send_response(200); self.send_header("Content-Type", "image/png"); self.send_header("Content-Length", str(len(png))); self.end_headers()
            self.wfile.write(png); return
        if path == "/api/library/views":                              # POST upload library items (multipart)
            if n > 200 * 2**20:
                return self._json({"error": "upload too large (200 MB max)"}, 413)
            form = _multipart(self.rfile.read(n), self.headers.get("Content-Type", ""))
            imgs = form.get("image") or []
            imgs = [i for i in (imgs if isinstance(imgs, list) else [imgs]) if isinstance(i, bytes) and len(i) > 100]
            if not imgs:
                return self._json({"error": "no image files"}, 400)
            labels = [l for l in re.split(r"[\n,;]+", str(form.get("labels") or "")) if l.strip()]
            grid = str(form.get("grid") or "auto")
            try:
                caption = float(form.get("caption") or (0.06 if grid == "strip" else 0.15))
            except ValueError:
                caption = 0.15
            try:
                top = float(form.get("top") or 0.03)
                group = int(form.get("group") or 1)
            except ValueError:
                top, group = 0.03, 1
            added = add_library(imgs, labels, str(form.get("kind") or "pose"), str(form.get("source") or ""), grid, caption, group, top)
            return self._json({"added": added, "count": len(added)})
        if path == "/api/characters":
            if n > 40 * 2**20:
                return self._json({"error": "image too large (40 MB max)"}, 413)
            form = _multipart(self.rfile.read(n), self.headers.get("Content-Type", ""))
            img = form.get("image")
            name = str(form.get("name", "")).strip()[:60]
            if not isinstance(img, bytes) or len(img) < 100 or not name:
                return self._json({"error": "need a name and an image"}, 400)
            cid = f"c{int(time.time())}{random.randint(100, 999)}"
            if not img.startswith(b"\x89PNG"):
                img = _to_png(img)
                if img is None:
                    return self._json({"error": "could not read that image; PNG or JPEG please"}, 400)
            (CHARS / f"{cid}.png").write_bytes(img)
            species = str(form.get("species") or "character").strip()[:40] or "character"
            (CHARS / f"{cid}.json").write_text(json.dumps({"id": cid, "name": name, "notes": str(form.get("notes", ""))[:400],
                                                            "species": species, "at": time.strftime("%Y-%m-%d %H:%M")}, indent=1))
            queued = queue_sheet(cid, species) if str(form.get("build_sheet", "1")) not in ("0", "false", "") else 0
            return self._json({"id": cid, "sheet_queued": queued})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                return self._json({"error": "JSON object expected"}, 400)
        except Exception:
            return self._json({"error": "bad json"}, 400)
        if path.startswith("/api/characters/") and path.endswith("/derive"):   # POST render own copies of library items
            cid = Path(path.split("/")[3]).name
            if not (CHARS / f"{cid}.png").exists():
                return self._json({"error": "no such character"}, 404)
            try:
                meta = json.loads((CHARS / f"{cid}.json").read_text())
            except Exception:
                meta = {}
            lib = library_items()
            tags = [str(t) for t in (body.get("tags") or []) if str(t) in lib]
            kinds = [k for k in (body.get("kinds") or []) if k in LIB_KINDS]
            if kinds:
                tags += [t for t, m in lib.items() if m.get("kind") in kinds and t not in tags]
            tags = [t for t in tags if lib[t].get("source") != cid]      # its own source items are already its views
            nq = queue_derive(cid, tags, meta.get("species") or "character")
            return self._json({"queued": nq, "pending": sheet_pending(cid)})
        if path.startswith("/api/characters/") and path.endswith("/sheet"):   # POST build the sheet
            cid = Path(path.split("/")[3]).name
            if not (CHARS / f"{cid}.png").exists():
                return self._json({"error": "no such character"}, 404)
            try:
                meta = json.loads((CHARS / f"{cid}.json").read_text())
            except Exception:
                meta = {}
            n = queue_sheet(cid, meta.get("species") or "character")
            return self._json({"queued": n, "pending": sheet_pending(cid), "have": list(sheet_of(cid))})
        if path == "/api/characters/from_image":
            iid = Path(str(body.get("image_id") or "")).name
            name = str(body.get("name") or "").strip()[:60]
            src = OUT / f"{iid}.png"
            if not iid or not src.exists() or not name:
                return self._json({"error": "need an existing picture and a name"}, 400)
            cid = f"c{int(time.time())}{random.randint(100, 999)}"
            shutil.copy(src, CHARS / f"{cid}.png")
            species = str(body.get("species") or "character").strip()[:40] or "character"
            (CHARS / f"{cid}.json").write_text(json.dumps({"id": cid, "name": name, "notes": str(body.get("notes") or "")[:400],
                                                            "species": species, "from_image": iid, "at": time.strftime("%Y-%m-%d %H:%M")}, indent=1))
            queued = queue_sheet(cid, species) if str(body.get("build_sheet", "1")) not in ("0", "false", "") else 0
            return self._json({"id": cid, "sheet_queued": queued})
        if path == "/api/feedback":
            iid = Path(str(body.get("image_id") or "")).name
            if not iid or not (OUT / f"{iid}.json").exists():
                return self._json({"error": "no such picture"}, 404)
            v = body.get("verdict")
            verdict = "up" if v in ("up", "good", 1, True) else "down" if v in ("down", "bad", 0, False) else None
            if verdict is None:                                     # a missing or unknown verdict is not a thumbs-down
                return self._json({"error": "verdict must be 'up' or 'down'"}, 400)
            job = json.loads((OUT / f"{iid}.json").read_text())
            issues = [str(x) for x in (body.get("issues") or []) if str(x) in fb.ISSUES]
            note = str(body.get("note") or "").strip()[:1000]
            crit = fb.get_critique(iid) or job.get("critique")
            if crit and "error" in crit:
                crit = None
            if verdict == "down" and not issues:
                issues = sorted({c["category"] for c in (crit or {}).get("issues") or []}) or (["other"] if note else [])
            res = fb.record(iid, verdict, issues, note, job, crit)
            if res.get("repair"):
                res["repair"]["feedback_id"] = res["id"]; res["repair"]["repair_of"] = iid
            return self._json(res)
        if path == "/api/feedback/teach":                                   # an approved picture becomes the character's own view
            iid = Path(str(body.get("image_id") or "")).name
            if not re.fullmatch(r"\d+-\d+", iid) or not (OUT / f"{iid}.png").exists() or not (OUT / f"{iid}.json").exists():
                return self._json({"error": "no such picture"}, 404)
            job = json.loads((OUT / f"{iid}.json").read_text())
            cid = fb.character_of(job)
            if not cid or not _cid_ok(cid) or not (CHARS / f"{cid}.png").exists():
                return self._json({"error": "this picture is not of a saved character"}, 400)
            label = re.sub(r"\s+", " ", str(body.get("label") or "")).strip()[:70]
            if not label:
                return self._json({"error": "name the pose: what the character is doing"}, 400)
            kind = body.get("kind") if body.get("kind") in ("pose", "activity", "expression") else "pose"
            added = add_views(cid, [(OUT / f"{iid}.png").read_bytes()], [label], "none", 0.0, kind, source="taught", origin=iid)
            return self._json({"character": cid, "added": added})
        if path == "/api/feedback/traits":                                  # correct a character's feature list, or read it again
            cid = Path(str(body.get("character") or "")).name
            if not cid or not (CHARS / f"{cid}.png").exists():
                return self._json({"error": "no such character"}, 404)
            res = fb.set_traits(cid, body.get("traits"), bool(body.get("reread")))
            return self._json(res) if res and (res.get("traits") or not body.get("reread")) else self._json({"error": "the vision model could not read the reference picture"}, 502)
        if path == "/api/feedback/critique":                                # run (or re-run) the vision check on one picture
            iid = Path(str(body.get("image_id") or "")).name
            if not iid or not (OUT / f"{iid}.json").exists():
                return self._json({"error": "no such picture"}, 404)
            job = json.loads((OUT / f"{iid}.json").read_text())
            res = _critique_job(job)
            if not res:
                return self._json({"error": "critic unavailable"}, 502)
            issues = sorted({c["category"] for c in res.get("issues") or []})
            return self._json({"critique": res, "suggested_issues": issues,
                               "diagnosis": fb.diagnose(job, issues, "", res) if issues else [],
                               "repair": fb.build_repair(job, issues, "", res) if issues else None})
        if path == "/api/lessons":
            L = fb.create_lesson(str(body.get("category") or "other"), str(body.get("title") or "untitled rule"),
                                 [str(k) for k in (body.get("keywords") or []) if str(k).strip()], append=str(body.get("append") or ""),
                                 prepend=str(body.get("prepend") or ""), guidance_min=_num(body.get("guidance_min"), None, float),
                                 steps_min=_num(body.get("steps_min"), None, int), why=str(body.get("why") or ""), source="user", enabled=1)
            return self._json(L)
        if path == "/api/lessons/learn":
            return self._json(fb.learn(int(_num(body.get("n"), 3, int)), int(_num(body.get("limit"), 25, int))))
        if path == "/api/compile":                                     # rewrite a rough prompt with the chat model
            prompt = str(body.get("prompt") or "").strip()[:2000]
            if not prompt:
                return self._json({"error": "empty prompt"}, 400)
            cids = [Path(str(c)).name for c in (body.get("characters") or []) if (CHARS / f"{Path(str(c)).name}.png").exists()][:1]
            names = []
            for c in cids:
                try:
                    names.append(json.loads((CHARS / f"{c}.json").read_text()).get("name") or c)
                except Exception:
                    names.append(c)
            try:
                reply = chat(str(body.get("model") or CHAT_MODEL), [{"role": "user", "content": prompt}], names,
                             cids[0] if cids else None, COMPILE_SYSTEM)
            except Exception as e:
                return self._json({"error": f"ollama: {str(e)[:200]}"}, 502)
            m = re.search(r"```(?:prompt)?[^\n]*\n([\s\S]*?)```", reply)
            text = (m.group(1) if m else reply).strip().strip('"').strip()
            out = {"prompt": text}
            if cids and text:
                out["plan"] = plan_refs(text, cids[0])
            return self._json(out)
        if path == "/api/chat":
            msgs = [m for m in body.get("messages", []) if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
            if not msgs:
                return self._json({"error": "no messages"}, 400)
            try:
                names = [str(x)[:60] for x in (body.get("characters") or [])][:2]
                cids = [Path(str(c)).name for c in (body.get("character_ids") or []) if (CHARS / f"{Path(str(c)).name}.png").exists()]
                return self._json({"reply": chat(str(body.get("model") or CHAT_MODEL), msgs, names, cids[0] if cids else None)})
            except Exception as e:
                return self._json({"error": f"ollama: {str(e)[:200]}"}, 502)
        if path != "/api/generate":
            return self._not_found(path)
        prompt = str(body.get("prompt", "")).strip()[:2000]
        if not prompt:
            return self._json({"error": "empty prompt"}, 400)
        mid = body.get("model") if body.get("model") in MODELS else pick_default()
        m = MODELS[mid]
        if not m["builder"] or not _exists(*m["files"]):
            return self._json({"error": f"{m['label']} is not installed"}, 400)
        mode = m.get("mode", "txt2img")
        chars = [str(c) for c in (body.get("characters") or []) if isinstance(c, (str, int))][:MAX_CHARACTERS]
        if body.get("character"):                       # old single-character field
            chars = [str(body["character"])]
        edit_of = str(body.get("edit_of") or "")
        if mode == "character":
            chars = [c for c in chars if (CHARS / f"{c}.png").exists()]
            if not chars:
                return self._json({"error": "pick one or two saved characters for this model"}, 400)
        elif mode == "edit":
            src = edit_source(edit_of) if edit_of else None
            if not src or not src.exists():
                return self._json({"error": "pick a picture to edit"}, 400)
            chars = []
        else:
            chars, edit_of = [], ""
        w, h = SIZES.get(body.get("size", "square"), SIZES["square"])
        ref = str(body.get("reference") or "auto")
        if ref.startswith("lib:"):
            ref = "lib:" + Path(ref[4:]).name
            if ref[4:] not in library_items():
                ref = "auto"
        elif ref not in ("auto", "original") and not (chars and (sheet_dir(chars[0]) / f"{Path(ref).name}.png").exists()):
            ref = "auto"
        garments = body.get("garments", "auto")
        if isinstance(garments, list):
            garments = [str(t).removeprefix("lib:") for t in garments][:MAX_GARMENTS]
        else:
            garments = "auto"
        extra = []                                      # further reference pictures (a second character, a prop): picture ids or char:<id>[:<view>]
        for x in (body.get("extra_refs") or [])[:2]:
            s = edit_source(str(x)) if isinstance(x, (str, int)) else None
            if s and s.exists():
                extra.append(str(x))
        loras = []                                      # [{"name": <file in models/loras>, "strength": 0-2}] or plain file names
        for x in (body.get("loras") or [])[:3]:
            name = Path(str(x.get("name") if isinstance(x, dict) else x)).name
            if name and (M / "loras" / name).exists():
                loras.append({"name": name, "strength": max(0.0, min(2.0, _num(x.get("strength") if isinstance(x, dict) else None, 1.0, float)))})
        job = {"id": f"{int(time.time())}-{random.randint(1000, 9999)}", "prompt": prompt, "model": mid,
               "characters": chars, "edit_of": edit_of or None, "reference": ref, "garments": garments, "extra_refs": extra, "loras": loras,
               "guides": body.get("guides", True) is not False,
               "repair_of": Path(str(body.get("repair_of") or "")).name or None, "retry_of": Path(str(body.get("retry_of") or "")).name or None,
               "feedback_id": _num(body.get("feedback_id"), None, int), "guide": _guide_spec(body.get("guide")) if body.get("repair_of") else None,
               "style": body.get("style") if body.get("style") in STYLES else "none",
               "w": w, "h": h, "steps": max(1, min(60, _num(body.get("steps"), m["steps"], int))),
               "guidance": max(0.0, min(15.0, _num(body.get("guidance"), m["guidance"] or 0, float))),
               "seed": _num(body.get("seed"), None, int) or random.randint(1, 2**31),
               "status": "queued", "at": time.strftime("%Y-%m-%d %H:%M")}
        with lock:
            state["queue"].append(job)
        save_queue()
        self._json({"id": job["id"]})


if __name__ == "__main__":
    fb.init(HERE / "data" / "feedback.db", OUT, ollama=OLLAMA_URL, critic_model=CRITIC_MODEL, learner_model=LEARNER_MODEL,
            max_refs=MAX_REFS, expr_words=_expr_words, sizes=SIZES,
            match_pose=lambda text: _best_by_kind(_expand(" " + re.sub(r"[^a-z0-9 ]+", " ", text.lower()) + " "), "", POSE_KINDS, {}, library_items()))
    load_queue()
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=idle_watch, daemon=True).start()
    print(f"picgen on http://{HOST}:{PORT} (ComfyUI {COMFY_DIR}, idle stop {IDLE_MINUTES} min, default {pick_default()})")
    try:
        ThreadingHTTPServer((HOST, PORT), H).serve_forever()
    finally:
        comfy_stop()
