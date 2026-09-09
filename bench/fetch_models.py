"""Pre-download model weights so benchmark timing separates download from compute.

Run with a repo id as argv[1]. HF traffic goes through hf-mirror.com because the
machine is behind the GFW; the env var must be set before huggingface_hub loads.
"""
import os
import sys
import time

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from huggingface_hub import snapshot_download

repo = sys.argv[1]
allow = sys.argv[2].split(",") if len(sys.argv) > 2 else None

t0 = time.time()
path = snapshot_download(repo_id=repo, allow_patterns=allow)
print(f"[fetch] {repo} -> {path} in {time.time() - t0:.1f}s", flush=True)
