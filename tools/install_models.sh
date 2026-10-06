#!/usr/bin/env bash
# Download the image models picgen uses into ComfyUI's models folder.
#
#   tools/install_models.sh                 # core set: z-image + qwen (commercial-safe character/edit)
#   tools/install_models.sh all             # everything below (about 76 GB)
#   tools/install_models.sh z-image qwen flux-kontext flux-dev toonyou   # pick sets
#
# COMFY_DIR (default ~/ComfyUI) must hold ComfyUI with its venv at COMFY_DIR/venv.
# Downloads are resumable (curl -C -); rerun after an interruption. Nothing is re-downloaded
# when the file is already there with the expected size.
#
# Sources are public Hugging Face repositories (no login) and, for ToonYou, Civitai.
# Licenses: Z-Image Turbo and Qwen-Image-Edit 2511 are Apache 2.0; FLUX.1 Kontext [dev] and
# FLUX.1-dev are non-commercial (Black Forest Labs); ToonYou is CreativeML OpenRAIL-M.
set -euo pipefail

COMFY_DIR="${COMFY_DIR:-$HOME/ComfyUI}"
M="$COMFY_DIR/models"
HF="https://huggingface.co"
sets=("$@"); [ ${#sets[@]} -eq 0 ] && sets=(z-image qwen)
[[ " ${sets[*]} " == *" all "* ]] && sets=(z-image qwen flux-kontext flux-dev toonyou)

[ -d "$COMFY_DIR" ] || { echo "COMFY_DIR=$COMFY_DIR does not exist. Install ComfyUI first." >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }

# fetch <url> <relative path under models/> <approx GB, for the message>
fetch() {
  local url="$1" rel="$2" gb="$3" dst="$M/$2"
  mkdir -p "$(dirname "$dst")"
  if [ -f "$dst" ]; then
    local remote local
    remote=$(curl -sIL "$url" | grep -i '^content-length:' | tail -1 | tr -dc '0-9')
    local=$(stat -c %s "$dst")
    if [ -n "$remote" ] && [ "$remote" = "$local" ]; then echo "  have   $rel"; return; fi
    echo "  resume $rel"
  else
    echo "  get    $rel  (~${gb} GB)"
  fi
  curl -L --fail --retry 5 --retry-delay 5 -C - -o "$dst" "$url"
}

for s in "${sets[@]}"; do
  echo "== $s"
  case "$s" in
    z-image)   # Z-Image Turbo: text to image, Apache 2.0. 17 GB. Also provides the FLUX VAE (ae.safetensors).
      fetch "$HF/Comfy-Org/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_bf16.safetensors" diffusion_models/z_image_turbo_bf16.safetensors 11.5
      fetch "$HF/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b_fp8_mixed.safetensors" text_encoders/qwen_3_4b_fp8_mixed.safetensors 5.2
      fetch "$HF/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors" vae/ae.safetensors 0.3 ;;
    qwen)      # Qwen-Image-Edit 2511 (character + edit), Apache 2.0. 25.5 GB + the ComfyUI-GGUF custom node.
      fetch "$HF/unsloth/Qwen-Image-Edit-2511-GGUF/resolve/main/qwen-image-edit-2511-Q6_K.gguf" unet/qwen-image-edit-2511-Q6_K.gguf 15.7
      fetch "$HF/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors" loras/Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors 0.8
      fetch "$HF/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors" text_encoders/qwen/qwen_2.5_vl_7b_fp8_scaled.safetensors 8.7
      fetch "$HF/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors" vae/qwen_image_vae.safetensors 0.2
      if [ ! -d "$COMFY_DIR/custom_nodes/ComfyUI-GGUF" ]; then
        echo "  node   ComfyUI-GGUF (city96)"
        git clone --depth 1 https://github.com/city96/ComfyUI-GGUF "$COMFY_DIR/custom_nodes/ComfyUI-GGUF"
      else echo "  have   custom_nodes/ComfyUI-GGUF"; fi
      if [ -x "$COMFY_DIR/venv/bin/pip" ]; then
        "$COMFY_DIR/venv/bin/pip" install -q -r "$COMFY_DIR/custom_nodes/ComfyUI-GGUF/requirements.txt"
      else echo "  WARN   no venv at $COMFY_DIR/venv; run: pip install -r custom_nodes/ComfyUI-GGUF/requirements.txt in ComfyUI's Python" >&2; fi ;;
    flux-kontext)  # FLUX.1 Kontext [dev] (character + edit), non-commercial. 16.4 GB. Needs vae/ae.safetensors from the z-image set.
      fetch "$HF/Comfy-Org/flux1-kontext-dev_ComfyUI/resolve/main/split_files/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors" diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors 11.1
      fetch "$HF/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors" text_encoders/clip_l.safetensors 0.2
      fetch "$HF/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn_scaled.safetensors" text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors 4.8
      fetch "$HF/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors" vae/ae.safetensors 0.3 ;;
    flux-dev)  # FLUX.1-dev fp8 checkpoint (photoreal text to image), non-commercial. 16 GB, text encoders included.
      fetch "$HF/Comfy-Org/flux1-dev/resolve/main/flux1-dev-fp8.safetensors" checkpoints/flux1-dev-fp8.safetensors 16.1 ;;
    toonyou)   # ToonYou beta 6 (SD 1.5 cartoon), CreativeML OpenRAIL-M. 2.1 GB, from Civitai.
      fetch "https://civitai.com/api/download/models/125771?fileId=90840" checkpoints/toonyou_beta6.safetensors 2.1 ;;
    *) echo "unknown set: $s (use z-image, qwen, flux-kontext, flux-dev, toonyou, all)" >&2; exit 2 ;;
  esac
done

echo
echo "Done. If picgen is running, GET /api/image_models now lists these as installed (no restart needed)."
echo "Storage used by models: $(du -sh "$M" 2>/dev/null | cut -f1)"
