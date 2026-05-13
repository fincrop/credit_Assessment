#!/usr/bin/env python3
"""
Script to update NEXT_PUBLIC_APP_DOMAIN in .env.local file
"""

import os
import re

def update_env_domain():
    env_file = "frontend/.env.local"
    domain = "https://occupational-points-asia-gallery.trycloudflare.com"
    
    if not os.path.exists(env_file):
        print(f"Error: {env_file} not found")
        return False
    
    # Read current content
    with open(env_file, 'r') as f:
        content = f.read()
    
    # Update or add NEXT_PUBLIC_APP_DOMAIN
    if 'NEXT_PUBLIC_APP_DOMAIN=' in content:
        # Replace existing line
        content = re.sub(
            r'NEXT_PUBLIC_APP_DOMAIN=.*',
            f'NEXT_PUBLIC_APP_DOMAIN={domain}',
            content
        )
        print("Updated existing NEXT_PUBLIC_APP_DOMAIN")
    else:
        # Add new line
        content += f'\nNEXT_PUBLIC_APP_DOMAIN={domain}\n'
        print("Added new NEXT_PUBLIC_APP_DOMAIN")
    
    # Write back
    with open(env_file, 'w') as f:
        f.write(content)
    
    print(f"Set NEXT_PUBLIC_APP_DOMAIN to: {domain}")
    print("Please restart npm run dev for changes to take effect")
    return True

if __name__ == "__main__":
    update_env_domain()
