# Teardown & Storage Restore Guide

Documents everything installed for this project and how to fully uninstall it to reclaim disk space.

## What Was Installed

| Thing | Location | Size (approx) |
|---|---|---|
| nvm (via Homebrew) | `$(brew --prefix nvm)` | ~10 MB |
| Node.js 20 (via nvm) | `~/.nvm/versions/node/v20.x.x/` | ~200 MB |
| Kaetram-Open repo | `~/projects/Kaetram-Open/` | ~100 MB |
| Kaetram node_modules | `~/projects/Kaetram-Open/node_modules/` + workspace `packages/*/node_modules/` | ~800 MB–1.5 GB |
| Kaetram build output | `~/projects/Kaetram-Open/packages/*/dist/` | ~200 MB |
| websockets (Python) | system Python site-packages | ~1 MB |
| **Total** | | **~1.3–2 GB** |

---

## Teardown Steps

### 1. Stop the Kaetram server (if running)

```bash
pkill -f "yarn start" 2>/dev/null || true
pkill -f "kaetram" 2>/dev/null || true
```

### 2. Remove Kaetram-Open

```bash
rm -rf ~/projects/Kaetram-Open
# If ~/projects is now empty and you didn't have it before:
rmdir ~/projects 2>/dev/null || true
```

### 3. Remove Node.js 20 installed via nvm

```bash
export NVM_DIR="$HOME/.nvm"
source "$(brew --prefix nvm)/nvm.sh"
nvm uninstall 20
```

### 4. Remove nvm itself (installed via Homebrew)

```bash
brew uninstall nvm
# Remove the nvm data directory:
rm -rf ~/.nvm
```

### 5. Remove websockets Python package

```bash
python3 -m pip uninstall websockets --break-system-packages -y
```

### 6. Clean up generated runtime files in this repo

```bash
# Remove the observer state file (written at runtime)
rm -f state/game_state.json state/game_state.tmp

# Remove dataset frames (large PNGs — already gitignored)
rm -rf dataset/*/frames/
```

---

## Verify Storage Reclaimed

```bash
# Check Kaetram is gone
ls ~/projects/Kaetram-Open 2>/dev/null && echo "STILL EXISTS" || echo "removed ✓"

# Check nvm is gone
ls ~/.nvm 2>/dev/null && echo "STILL EXISTS" || echo "removed ✓"

# Check Node 20 is gone
node --version  # should show your system node (not 20.x)
```

---

## Keep But Trim (optional)

If you want to keep the server available but reclaim most space, just delete node_modules and build output. Re-running `yarn install && yarn build` restores them.

```bash
# Remove only the heavy stuff (~1 GB), keep source
find ~/projects/Kaetram-Open -name "node_modules" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find ~/projects/Kaetram-Open -name "dist" -type d -prune -exec rm -rf {} + 2>/dev/null || true
```

To restore:
```bash
export NVM_DIR="$HOME/.nvm" && source "$(brew --prefix nvm)/nvm.sh" && nvm use 20
cd ~/projects/Kaetram-Open && yarn install && yarn build
```

---

## Re-setup From Scratch

If you uninstalled everything and want to come back:

```bash
# 1. Install nvm
brew install nvm
mkdir -p ~/.nvm

# 2. Add to your shell rc (~/.zshrc or ~/.zprofile):
# export NVM_DIR="$HOME/.nvm"
# source "$(brew --prefix nvm)/nvm.sh"

# 3. Install Node 20
source "$(brew --prefix nvm)/nvm.sh"
nvm install 20

# 4. Clone and build Kaetram
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/Kaetram/Kaetram-Open.git
cd Kaetram-Open
printf "ACCEPT_LICENSE=true\nSKIP_DATABASE=true\nTUTORIAL_ENABLED=false\n" > .env
yarn install && yarn build

# 5. Install Python websockets
python3 -m pip install websockets --break-system-packages

# 6. Start the server
ACCEPT_LICENSE=true SKIP_DATABASE=true yarn start
```
