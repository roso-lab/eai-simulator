#!/usr/bin/env bash
# Compile the C++ validation harnesses for the usd-to-gazebo skill.
# Requires libgazebo-dev (gazebo11) and pkg-config.
set -euo pipefail
cd "$(dirname "$0")"

g++ -O2 meshcheck.cpp -o meshcheck $(pkg-config --cflags --libs gazebo)
g++ -O2 -shared -fPIC worldprobe.cpp -o libworldprobe.so $(pkg-config --cflags --libs gazebo)
echo "built: meshcheck, libworldprobe.so"
