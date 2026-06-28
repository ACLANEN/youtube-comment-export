#!/usr/bin/env python3
"""Comment Research by Fynn - Launcher"""
import os, sys, hashlib

# Integrity check
EXPECTED = "29b3887c9eb49d057cc455db8059aa89e3d88fe93f2fadb09cd9189886080a80"
actual = hashlib.sha256(open(os.path.join(os.path.dirname(__file__),"app.pyc"),"rb").read()).hexdigest()
if actual != EXPECTED:
    print("⚠️ 文件完整性校验失败，请重新下载")
    sys.exit(1)

# Run the app
import app
app.app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=False)
