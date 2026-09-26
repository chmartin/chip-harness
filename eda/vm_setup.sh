#!/usr/bin/env bash
# One-time setup for an x86 Ubuntu 22.04/24.04 VM (e.g. AWS EC2 c7i.8xlarge: 32 vCPU / 64 GB).
# Usage on the VM:  bash vm_setup.sh   then log out/in once (docker group), then:
#   PAR=12 bash eda_feasibility_test.sh
set -euo pipefail
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl git htop tmux python3-pip
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
sudo docker pull openroad/orfs:latest
echo "Done. Log out and back in (for docker group), then: PAR=12 bash eda_feasibility_test.sh"
