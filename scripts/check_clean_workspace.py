import subprocess
import sys

status = subprocess.check_output(
    ["git", "status", "--porcelain"],
    text=True
)

if status.strip():
    print("Workspace is not clean!")
    print(status)
    sys.exit(1)
else:
    print("Workspace is clean.")
