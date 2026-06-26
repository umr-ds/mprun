#!/usr/bin/env bash
# Example run script — receives parameters as --name value arguments.
set -euo pipefail

echo "Running with parameters: $*"

# Write a dummy output file for the results collection
mkdir -p output
echo "Completed run with args: $*" > output.txt
