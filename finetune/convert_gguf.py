"""
Convert the merged safetensors model to GGUF Q4_K_M.

The training job saved the merged model but GGUF export failed.
This script picks up from there.

Usage:
    modal run finetune/convert_gguf.py
"""

import modal

app = modal.App("kaetram-gguf-convert")

checkpoint_vol = modal.Volume.from_name("kaetram-model-vol", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "cmake", "build-essential")
    .run_commands(
        "git clone --depth 1 https://github.com/ggml-org/llama.cpp /llama.cpp",
        "cd /llama.cpp && cmake -B build && cmake --build build -j$(nproc) --target llama-quantize",
        "pip install -r /llama.cpp/requirements/requirements-convert_hf_to_gguf.txt",
    )
    .uv_pip_install(
        "transformers>=5.0.0",
        "torch>=2.7.0",
        "sentencepiece",
        "protobuf",
    )
    .env({"HF_HOME": "/model_cache"})
)

MODEL_DIR = "/checkpoints/kaetram-qwen3.5-9b-r4-multiturn/gguf"
F16_FILE = "/checkpoints/kaetram-qwen3.5-9b-r4-multiturn/kaetram-f16.gguf"
OUTPUT_FILE = "/checkpoints/kaetram-qwen3.5-9b-r4-multiturn/kaetram-q4_k_m.gguf"


@app.function(
    image=image,
    gpu="T4",
    timeout=1800,
    volumes={"/checkpoints": checkpoint_vol},
)
def convert():
    import subprocess
    import os

    # Step 1: Convert HF safetensors → GGUF f16
    print(f"Step 1: Converting {MODEL_DIR} to GGUF f16...")
    result = subprocess.run(
        [
            "python", "/llama.cpp/convert_hf_to_gguf.py",
            MODEL_DIR,
            "--outtype", "f16",
            "--outfile", F16_FILE,
        ],
        capture_output=True,
        text=True,
    )
    print(result.stdout[-2000:] if result.stdout else "")
    if result.returncode != 0:
        print(f"STDERR: {result.stderr[-2000:]}")
        raise RuntimeError(f"f16 conversion failed with code {result.returncode}")

    f16_size = os.path.getsize(F16_FILE) / 1e9
    print(f"f16 GGUF: {f16_size:.1f} GB")

    # Step 2: Quantize f16 → Q4_K_M
    print(f"\nStep 2: Quantizing to Q4_K_M...")
    result = subprocess.run(
        ["/llama.cpp/build/bin/llama-quantize", F16_FILE, OUTPUT_FILE, "Q4_K_M"],
        capture_output=True,
        text=True,
    )
    print(result.stdout[-2000:] if result.stdout else "")
    if result.returncode != 0:
        print(f"STDERR: {result.stderr[-2000:]}")
        raise RuntimeError(f"Quantization failed with code {result.returncode}")

    size_gb = os.path.getsize(OUTPUT_FILE) / 1e9
    print(f"\nQ4_K_M GGUF: {OUTPUT_FILE} ({size_gb:.1f} GB)")

    # Clean up f16 to save volume space
    os.remove(F16_FILE)

    checkpoint_vol.commit()

    print(f"\nDownload with:")
    print(f"  modal volume get kaetram-model-vol /kaetram-qwen3.5-9b-r4-multiturn/kaetram-q4_k_m.gguf ./kaetram-r4-q4_k_m.gguf")
    print(f"\nThen on your RTX 3060:")
    print(f"  ollama create kaetram -f <(echo 'FROM ./kaetram-r4-q4_k_m.gguf')")
    print(f"  ollama run kaetram")
    return size_gb


@app.local_entrypoint()
def main():
    size = convert.remote()
    print(f"\nDone! GGUF is {size:.1f} GB — ready for your RTX 3060.")
