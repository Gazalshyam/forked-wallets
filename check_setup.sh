#!/usr/bin/env bash
# Forked Wallets: checks that this Ubuntu machine is ready to build and run the project.
# Usage:  bash check_setup.sh            (run it from the project folder)
#         bash check_setup.sh --docker-run   (also pulls and runs Docker's hello-world image)

pass=0; warn=0; fail=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass+1)); }
note() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; warn=$((warn+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail+1)); }
has()  { command -v "$1" >/dev/null 2>&1; }

echo "== System =="
if [ -r /etc/os-release ]; then
  . /etc/os-release
  case "$ID" in
    ubuntu) ok "OS: $PRETTY_NAME" ;;
    *)      note "OS is $PRETTY_NAME (these steps were written for Ubuntu 24.04)" ;;
  esac
else
  note "Could not read /etc/os-release"
fi

mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null)
if [ -n "$mem_kb" ]; then
  mem_gb=$((mem_kb / 1024 / 1024))
  if [ "$mem_kb" -ge 3800000 ]; then ok "RAM: about ${mem_gb} GB"
  else bad "RAM: about ${mem_gb} GB (Claude Code needs 4 GB or more)"; fi
fi

free_gb=$(df -BG --output=avail "$HOME" 2>/dev/null | tail -1 | tr -dc '0-9')
if [ -n "$free_gb" ]; then
  if [ "$free_gb" -ge 10 ]; then ok "Free disk in home: ${free_gb} GB"
  else note "Free disk in home: ${free_gb} GB (Docker images need roughly 5 to 10 GB)"; fi
fi

echo "== Basic tools =="
for t in curl git unzip zip; do
  if has "$t"; then ok "$t installed"; else bad "$t missing  ->  sudo apt install -y $t"; fi
done

if has python3; then
  pyv=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    ok "python3 $pyv (needed only for scripts/paypal_spike.py)"
  else
    note "python3 $pyv is old; the PayPal spike script expects 3.10 or newer"
  fi
else
  bad "python3 missing  ->  sudo apt install -y python3"
fi

echo "== Docker =="
if has docker; then
  ok "docker installed: $(docker --version 2>/dev/null)"
  if docker compose version >/dev/null 2>&1; then
    ok "docker compose plugin: $(docker compose version --short 2>/dev/null)"
  else
    bad "docker compose plugin missing  ->  sudo apt install -y docker-compose-plugin"
  fi
  if docker info >/dev/null 2>&1; then
    ok "docker works without sudo and the daemon is running"
    if [ "$1" = "--docker-run" ]; then
      if docker run --rm hello-world >/dev/null 2>&1; then ok "docker can pull and run a container"
      else bad "docker run hello-world failed (check your internet connection)"; fi
    fi
  else
    bad "docker needs sudo or the daemon is stopped  ->  sudo usermod -aG docker \$USER, then log out and back in; sudo systemctl start docker"
  fi
else
  bad "docker missing (see the install steps)"
fi

echo "== Claude Code =="
if has claude; then
  ok "claude installed: $(claude --version 2>/dev/null | head -1)"
else
  if [ -x "$HOME/.local/bin/claude" ]; then
    bad "claude is installed but not on PATH  ->  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
  else
    bad "claude missing  ->  curl -fsSL https://claude.ai/install.sh | bash"
  fi
fi

echo "== Network =="
reach() {
  if curl -s -o /dev/null --max-time 10 "$1"; then ok "can reach $2"; else bad "cannot reach $2 ($1)"; fi
}
if has curl; then
  reach https://claude.ai "claude.ai"
  reach https://api-m.sandbox.paypal.com "PayPal Sandbox API"
  reach https://registry-1.docker.io/v2/ "Docker Hub"
  reach https://pypi.org/simple/ "PyPI"
  reach https://registry.npmjs.org/ "npm registry"
fi

echo "== Ports =="
if has ss; then
  if ss -ltn 2>/dev/null | grep -q ':8000 '; then
    note "port 8000 is already in use; stop that program before running the app"
  else
    ok "port 8000 is free"
  fi
fi

echo "== Project folder ($(pwd)) =="
for f in CLAUDE.md IMPLEMENTATION_SPEC.md; do
  if [ -f "$f" ]; then ok "$f present"; else note "$f not found here (copy it into the project folder)"; fi
done
if [ -d .git ]; then ok "git repository initialised"; else note "no git repository yet  ->  git init"; fi
if [ -f compose.yaml ]; then
  if docker compose config -q >/dev/null 2>&1; then ok "compose.yaml is valid"
  else note "compose.yaml did not validate (normal before .env exists)"; fi
fi
if [ -f .env ]; then
  if grep -q '^PAYPAL_CLIENT_ID=.\+' .env; then ok ".env has a PayPal client ID"
  else note ".env has no PayPal client ID (the app will run in mock mode)"; fi
  if git check-ignore -q .env 2>/dev/null; then ok ".env is ignored by git"
  elif [ -d .git ]; then bad ".env is NOT ignored by git  ->  echo .env >> .gitignore"; fi
else
  note "no .env yet (created later from .env.example)"
fi

echo
echo "Result: $pass passed, $warn warnings, $fail failed"
if [ "$fail" -eq 0 ]; then echo "Ready to build."; else echo "Fix the FAIL lines above, then run this again."; fi
exit "$fail"
