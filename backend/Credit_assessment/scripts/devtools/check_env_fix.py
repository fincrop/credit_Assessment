#!/usr/bin/env python3
"""
Script to check and fix NEXT_PUBLIC_APP_DOMAIN in frontend/.env.local
"""

import os
import re
import sys
from pathlib import Path

# scripts/devtools → package root (parents[2]) → monorepo root (parents[1] of package)
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PACKAGE_ROOT.parents[1]


def check_and_fix_env():
    env_file = REPO_ROOT / "frontend" / ".env.local"
    domain = "https://occupational-points-asia-gallery.trycloudflare.com"

    print(f"Checking {env_file}...")

    if not env_file.exists():
        print(f"Error: {env_file} not found")
        return False

    # Read current content
    content = env_file.read_text(encoding="utf-8")

    print("Current content:")
    print(content)
    print("-" * 50)

    # Check if NEXT_PUBLIC_APP_DOMAIN is set correctly
    domain_pattern = r"NEXT_PUBLIC_APP_DOMAIN=(.+)"
    match = re.search(domain_pattern, content)

    if match:
        current_domain = match.group(1).strip()
        print(f"Current NEXT_PUBLIC_APP_DOMAIN: {current_domain}")

        if current_domain == domain:
            print("✓ NEXT_PUBLIC_APP_DOMAIN is already set correctly!")
        else:
            print(f"✗ NEXT_PUBLIC_APP_DOMAIN is incorrect")
            print(f"Expected: {domain}")
            print(f"Found: {current_domain}")

            # Fix it
            content = re.sub(
                r"NEXT_PUBLIC_APP_DOMAIN=.*",
                f"NEXT_PUBLIC_APP_DOMAIN={domain}",
                content,
            )
            env_file.write_text(content, encoding="utf-8")
            print(f"✓ Fixed NEXT_PUBLIC_APP_DOMAIN to: {domain}")
            print("Please restart npm run dev for changes to take effect")
    else:
        print("✗ NEXT_PUBLIC_APP_DOMAIN not found in file")
        content += f"\nNEXT_PUBLIC_APP_DOMAIN={domain}\n"
        env_file.write_text(content, encoding="utf-8")
        print(f"✓ Added NEXT_PUBLIC_APP_DOMAIN: {domain}")
        print("Please restart npm run dev for changes to take effect")

    return True


if __name__ == "__main__":
    ok = check_and_fix_env()
    sys.exit(0 if ok else 1)
