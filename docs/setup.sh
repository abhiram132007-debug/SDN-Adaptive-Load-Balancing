#!/bin/bash
# One-time setup for SDN Adaptive Load Balancing on a fresh Ubuntu machine
set -e

echo "==> Installing system packages..."
sudo apt update
sudo apt install -y git curl software-properties-common \
    python3 python3-pip python3-venv python3-psutil \
    mininet openvswitch-switch
sudo systemctl enable --now openvswitch-switch

echo "==> Installing Python 3.9 (needed by Ryu 4.34)..."
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.9 python3.9-venv python3.9-distutils

echo "==> Creating Ryu environment..."
python3.9 -m venv ~/ryu-env
source ~/ryu-env/bin/activate
python -m pip install --upgrade "pip<25.3" "setuptools<58" wheel
python -m pip install --no-build-isolation "ryu==4.34" "eventlet<0.30.3"
ryu-manager --version
deactivate

echo "==> Checking Mininet and psutil..."
python3 -c "import mininet, psutil; print('Mininet and psutil OK')"

echo ""
echo "Setup complete. Follow README section 7 to start the three terminals."
