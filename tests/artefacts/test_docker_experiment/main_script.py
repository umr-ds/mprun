#!/usr/bin/env python3
"""Simple script for testing purposes."""

import os
import sys
from pathlib import Path

# Check environment variables
if "FOO" not in os.environ or os.environ["FOO"] != "bar":
    print("Environment variable 'FOO' not set correctly")
    sys.exit(1)
if "TEST_VARIABLE" not in os.environ or os.environ["TEST_VARIABLE"] != "test_value":
    print("Environment variable 'TEST_VARIABLE' not set correctly")
    sys.exit(1)

# Check environment files
if not Path("/tmp/envfile").is_file():
    print("Environment file '/tmp/envfile' not found")
    sys.exit(1)

print("Testing stdout")
print("Testing stderr", file=sys.stderr)

# Create files and directories in /tmp
Path("/tmp/test_file.txt").write_text("Test content in /tmp")
Path("/tmp/test_dir").mkdir(exist_ok=True)
Path("/tmp/test_dir/nested_file.txt").write_text("Nested content in /tmp")

# Create files and directories in working directory
Path("working_file.txt").write_text("Test content in working dir")
Path("working_dir").mkdir(exist_ok=True)
Path("working_dir/nested_working_file.txt").write_text("Nested content in working dir")
