#!/usr/bin/env python3
"""
Merge LoRA adapter into base model and export GGUF for local inference.

After downloading the adapter weights from Modal:
    modal volume get kaetram-model-vol /output ./kaetram-model

Run this script:
    python finetune/merge_and_quantize.py ./kaetram-model/kaetram-qwen-lora/final

This produces a GGUF file you can run on your RTX 3060 12GB with ollama or llama.cpp.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Merge LoRA and export GGUF")
    parser.add_argument("adapter_dir", type=Path, help="Path to LoRA adapter directory")
    parser.add_argument("--output", type=Path, default=None, help="Output directory (default: adapter_dir/../merged)")
    parser.add_argument("--quant", default="Q4_K_M", help="GGUF quantization type (default: Q4_K_M)")
    args = parser.parse_args()

    adapter_dir = args.adapter_dir.resolve()
    output_dir = (args.output or adapter_dir.parent / "merged").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    merged_dir = output_dir / "merged-full"
    gguf_path = output_dir / f"kaetram-qwen-{args.quant.lower()}.gguf"

    print("Step 1: Merge LoRA adapter into base model...")
    print(f"  Adapter: {adapter_dir}")
    print(f"  Output:  {merged_dir}")

    try:
        from peft import PeftModel, AutoPeftModelForCausalLM
        from transformers import AutoTokenizer, AutoModelForCausalLM
        import torch
    except ImportError:
        print("ERROR: Install dependencies first:")
        print("  pip install peft transformers torch")
        sys.exit(1)

    # Load and merge
    model = AutoPeftModelForCausalLM.from_pretrained(
        str(adapter_dir),
        torch_dtype=torch.float16,
        device_map="cpu",  # merge on CPU to avoid VRAM issues
        trust_remote_code=True,
    )
    merged_model = model.merge_and_unload()
    merged_model.save_pretrained(str(merged_dir))

    tokenizer = AutoTokenizer.from_pretrained(str(adapter_dir), trust_remote_code=True)
    tokenizer.save_pretrained(str(merged_dir))
    print(f"  Merged model saved to {merged_dir}")

    # Step 2: Convert to GGUF
    print(f"\nStep 2: Convert to GGUF ({args.quant})...")

    # Check for llama.cpp convert script
    convert_script = None
    for candidate in [
        Path.home() / "projects" / "llama.cpp" / "convert_hf_to_gguf.py",
        Path("/usr/local/bin/convert_hf_to_gguf.py"),
    ]:
        if candidate.exists():
            convert_script = candidate
            break

    if convert_script is None:
        print("\n  llama.cpp not found. To convert to GGUF manually:")
        print(f"    1. Clone llama.cpp: git clone https://github.com/ggerganov/llama.cpp")
        print(f"    2. Run: python llama.cpp/convert_hf_to_gguf.py {merged_dir} --outtype {args.quant.lower()} --outfile {gguf_path}")
        print(f"\n  Or use ollama directly with the merged HF model:")
        print(f"    Create a Modelfile with: FROM {merged_dir}")
        print(f"    Then: ollama create kaetram-qwen -f Modelfile")
        return

    subprocess.run([
        sys.executable, str(convert_script),
        str(merged_dir),
        "--outtype", args.quant.lower(),
        "--outfile", str(gguf_path),
    ], check=True)

    print(f"\n  GGUF saved to: {gguf_path}")
    print(f"  Size: {gguf_path.stat().st_size / 1e9:.1f} GB")
    print(f"\n  Run with ollama:")
    print(f"    ollama create kaetram-qwen -f <(echo 'FROM {gguf_path}')")
    print(f"    ollama run kaetram-qwen")


if __name__ == "__main__":
    main()
