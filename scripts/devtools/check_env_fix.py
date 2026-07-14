#!/usr/bin/env python3
"""
Script to check and fix NEXT_PUBLIC_APP_DOMAIN in .env.local file
"""

import os
import re

def check_and_fix_env():
    env_file = "frontend/.env.local"
    domain = "https://occupational-points-asia-gallery.trycloudflare.com"
    
    print(f"Checking {env_file}...")
    
    if not os.path.exists(env_file):
        print(f"Error: {env_file} not found")
        return False
    
    # Read current content
    with open(env_file, 'r') as f:
        content = f.read()
    
    print("Current content:")
    print(content)
    print("-" * 50)
    
    # Check if NEXT_PUBLIC_APP_DOMAIN is set correctly
    domain_pattern = r'NEXT_PUBLIC_APP_DOMAIN=(.+)'
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
            content = re.sub(domain_pattern, f'NEXT_PUBLIC_APP_DOMAIN={domain}', content)
            
            with open(env_file, 'w') as f:
                f.write(content)
            
            print("✓ Fixed NEXT_PUBLIC_APP_DOMAIN")
    else:
        print("✗ NEXT_PUBLIC_APP_DOMAIN not found in .env.local")
        
        # Add it
        content += f'\nNEXT_PUBLIC_APP_DOMAIN={domain}\n'
        
        with open(env_file, 'w') as f:
            f.write(content)
        
        print("✓ Added NEXT_PUBLIC_APP_DOMAIN")
    
    print("\nFinal content:")
    with open(env_file, 'r') as f:
        print(f.read())
    
    print("\nNext steps:")
    print("1. Stop the current npm run dev process")
    print("2. Clear Next.js cache: rm -rf .next")
    print("3. Restart npm run dev")
    
    return True

if __name__ == "__main__":
    check_and_fix_env()
