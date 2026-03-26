# Setup: Kaetram Agent on RTX 3060 with Finetuned Qwen3.5-9B

You are setting up an autonomous game-playing AI agent on this machine. The agent plays Kaetram (a 2D pixel MMORPG) using a finetuned Qwen3.5-9B model running locally on the RTX 3060, with OpenCode as the agent harness and Playwright MCP for browser automation.

## What already exists on this machine

- **Ollama**: installed and running (`systemctl status ollama`), GPU-accelerated
- **Base model**: `qwen3.5:9b` already pulled in ollama
- **LoRA adapter**: `~/kaetram-adapter/` — finetuned on 5,162 gameplay turns
- **Merged model**: `~/kaetram-merged/` — full merged HF safetensors (if merge completed)
- **Python venv**: `~/kaetram-venv/` with transformers, peft, torch installed

## Step 1: Create the GGUF and load into Ollama

If `~/kaetram-merged/` exists with safetensors files, convert to GGUF:

```bash
# Install llama.cpp for conversion
git clone --depth 1 https://github.com/ggerganov/llama.cpp ~/llama.cpp
cd ~/llama.cpp && pip install -r requirements.txt

# Convert merged model to GGUF f16, then quantize to Q4_K_M
python convert_hf_to_gguf.py ~/kaetram-merged/ --outtype f16 --outfile ~/kaetram-f16.gguf
./build/bin/llama-quantize ~/kaetram-f16.gguf ~/kaetram-q4_k_m.gguf Q4_K_M

# Load into ollama
cat > ~/Modelfile << 'EOF'
FROM ~/kaetram-q4_k_m.gguf

PARAMETER num_ctx 8192
PARAMETER temperature 0.7
PARAMETER top_p 0.9

SYSTEM """You are an AI agent playing Kaetram, a 2D pixel MMORPG. You observe the game via structured game state and an ASCII map, then decide and execute actions. Follow the OODA loop: Observe, Orient, Decide, Act."""
EOF

ollama create kaetram -f ~/Modelfile
ollama run kaetram "test"  # verify it works
```

**If llama.cpp conversion fails** (tokenizer issues with Qwen3.5), use this alternative approach — Unsloth can export GGUF directly:

```bash
source ~/kaetram-venv/bin/activate
pip install "unsloth[cu128-torch270]>=2025.7.8"

python3 << 'PYEOF'
from unsloth import FastLanguageModel
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="/home/pnir41/kaetram-adapter",
    max_seq_length=2048,
    load_in_4bit=False,
    load_in_16bit=True,
)
model.save_pretrained_gguf("/home/pnir41/kaetram-gguf", tokenizer, quantization_method="q4_k_m")
PYEOF

ollama create kaetram -f <(echo "FROM /home/pnir41/kaetram-gguf/unsloth.Q4_K_M.gguf
PARAMETER num_ctx 8192")
```

**Verify the model runs on GPU:**
```bash
ollama run kaetram "What mobs should I fight at level 5?"
ollama ps  # should show "100% GPU"
```

## Step 2: Install OpenCode

```bash
# Install Node.js if not present
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Install OpenCode globally
npm install -g opencode

# Verify
opencode --version
```

## Step 3: Install Playwright MCP server

```bash
# Install the Playwright MCP server
npm install -g @playwright/mcp

# Install browser binaries
npx playwright install chromium
```

## Step 4: Configure OpenCode

Create the OpenCode config at the project root:

```bash
mkdir -p ~/kaetram-agent
cat > ~/kaetram-agent/opencode.jsonc << 'EOF'
{
  // LLM Provider — our finetuned Kaetram model via Ollama
  "provider": {
    "ollama": {
      "url": "http://localhost:11434",
      "model": "kaetram"
    }
  },

  // MCP servers — Playwright for browser automation
  "mcp": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp", "--headless"]
    }
  },

  // Context settings
  "context": {
    "maxTokens": 8192
  }
}
EOF
```

## Step 5: Set up the game client

The agent needs a Kaetram game client to connect to. Either:

**Option A: Connect to GCP-hosted server** (RECOMMENDED — already running):
- Game client: `http://35.224.227.251:9000`
- Game server WS: `ws://35.224.227.251:9001`
- The agent opens Chromium to `http://35.224.227.251:9000` and plays via Playwright MCP
- No local Kaetram install needed

**Option B: Run Kaetram locally (not recommended — use Option A):**
```bash
git clone https://github.com/Kaetram/Kaetram-Open ~/Kaetram-Open
cd ~/Kaetram-Open
nvm use 20  # MUST be Node 20, not 24+
yarn install && yarn build && yarn start
# Client on http://localhost:9000, server WS on :9001
```

## Step 6: Create the agent play script

```bash
cat > ~/kaetram-agent/play.sh << 'BASH'
#!/bin/bash
# Kaetram agent loop using OpenCode + finetuned Qwen3.5-9B
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
USERNAME="${1:-KaetramBot}"
SESSION=1

# Read system prompt
SYSTEM_PROMPT=$(cat "$PROJECT_DIR/prompts/system.md" 2>/dev/null || echo "You are playing Kaetram. Observe, decide, act.")

while true; do
    echo "[Session $SESSION] Starting agent as $USERNAME..."

    cd "$PROJECT_DIR"
    opencode -p "Login as $USERNAME/password123 to http://localhost:9000 and play the game. $SYSTEM_PROMPT" \
        --provider ollama \
        --model kaetram \
        2>&1 | tee "logs/session_${SESSION}_$(date +%Y%m%d_%H%M%S).log"

    SESSION=$((SESSION + 1))
    echo "[Session $SESSION] Restarting in 10s..."
    sleep 10
done
BASH
chmod +x ~/kaetram-agent/play.sh
```

## Step 7: Copy game prompts from GCP VM

The system prompt and game knowledge files should be copied from the GCP VM:

```bash
# From the GCP VM (patnir41@<GCP_IP>), SCP the prompts:
mkdir -p ~/kaetram-agent/prompts ~/kaetram-agent/logs ~/kaetram-agent/state
scp -r patnir41@<GCP_VM_IP>:~/projects/kaetram-agent/prompts/ ~/kaetram-agent/prompts/
scp patnir41@<GCP_VM_IP>:~/projects/kaetram-agent/state_extractor.js ~/kaetram-agent/
```

## Step 8: Run it

```bash
# Terminal 1: Make sure ollama is running with the model
ollama run kaetram "test" && echo "Model OK"

# Terminal 2: Start the agent
cd ~/kaetram-agent
./play.sh
```

## Architecture

```
ollama (kaetram model, RTX 3060 GPU)
    ↑
OpenCode CLI (agent loop, tool orchestration)
    ↑
Playwright MCP server (browser automation)
    ↑
Chromium browser → Kaetram game client (localhost:9000)
    ↑
Kaetram game server (localhost:9001 or remote)
```

## Troubleshooting

- **Ollama not using GPU**: Check `ollama ps` — should show "100% GPU". If not, check NVIDIA drivers: `nvidia-smi`
- **Context too short**: Ollama defaults to 4K context. Set `PARAMETER num_ctx 8192` in Modelfile or pass `--num-ctx 8192`
- **OpenCode can't find model**: Verify `ollama list` shows `kaetram`, and ollama API is at `http://localhost:11434`
- **Playwright can't find browser**: Run `npx playwright install chromium`
- **Model quality is poor**: This is a first finetune on 3,844 records with 1 epoch. Quality improves with more data collection runs + more epochs.

## Performance expectations

- **VRAM usage**: ~5-6GB for Q4_K_M (12GB card, plenty of headroom)
- **Inference speed**: ~20-40 tokens/sec on RTX 3060
- **Context window**: 8K tokens (sufficient for game state + reasoning)
- **Model quality**: Trained on 3,844 gameplay turns — knows combat, navigation, quest patterns, but may struggle with novel situations the training data didn't cover
