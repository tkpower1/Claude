import sys
print("Paste your RSA private key, then press Ctrl+D on a new line:")
content = sys.stdin.read().strip()
with open('/tmp/kalshi_rsa.pem', 'w') as f:
    f.write(content + '\n')
print(f"Saved {len(content)} bytes to /tmp/kalshi_rsa.pem")
