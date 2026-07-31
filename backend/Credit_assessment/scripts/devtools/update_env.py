#!/usr/bin/env python3
"""
Script to update NEXT_PUBLIC_APP_DOMAIN in frontend/.env.local
"""

import re
import sys
from pathlib import Path

# scripts/devtools → package root (parents[2]) → monorepo root (parents[1] of package)
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PACKAGE_ROOT.parents[1]


def update_env_domain():
    env_file = REPO_ROOT / "frontend" / ".env.local"
    domain = "https://occupational-points-asia-gallery.trycloudflare.com"

    if not env_file.exists():
        print(f"Error: {env_file} not found")
        return False

    content = env_file.read_text(encoding="utf-8")

    # Update or add NEXT_PUBLIC_APP_DOMAIN
    if "NEXT_PUBLIC_APP_DOMAIN=" in content:
        content = re.sub(
            r"NEXT_PUBLIC_APP_DOMAIN=.*",
            f"NEXT_PUBLIC_APP_DOMAIN={domain}",
            content,
        )
        print("Updated existing NEXT_PUBLIC_APP_DOMAIN")
    else:
        content += f"\nNEXT_PUBLIC_APP_DOMAIN={domain}\n"
        print("Added new NEXT_PUBLIC_APP_DOMAIN")

    env_file.write_text(content, encoding="utf-8")

    print(f"Set NEXT_PUBLIC_APP_DOMAIN to: {domain}")
    print("Please restart npm run dev for changes to take effect")
    return True


if __name__ == "__main__":
    ok = update_env_domain()
    sys.exit(0 if ok else 1)
