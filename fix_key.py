import re
import os
from cryptography.fernet import Fernet

def fix_key():
    key = Fernet.generate_key().decode()
    app_path = 'app.py'
    
    if not os.path.exists(app_path):
        print(f"Error: {app_path} not found.")
        return

    with open(app_path, 'r') as f:
        content = f.read()
    
    # regex to find app.config['FERNET_KEY'] = '...'
    pattern = r"app\.config\['FERNET_KEY'\] = '.*'"
    replacement = f"app.config['FERNET_KEY'] = '{key}'"
    
    new_content = re.sub(pattern, replacement, content)
    
    with open(app_path, 'w') as f:
        f.write(new_content)
    
    print(f"Successfully updated app.py with key: {key}")
    
    # Verification
    try:
        Fernet(key)
        print("Key verification successful.")
    except Exception as e:
        print(f"Key verification failed: {e}")

if __name__ == '__main__':
    fix_key()
