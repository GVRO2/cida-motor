import os
import sys
import subprocess

def main():
    # Redirects arguments to token_optimizer.py
    dir_path = os.path.dirname(os.path.abspath(__file__))
    optimizer_path = os.path.join(dir_path, "token_optimizer.py")

    args = sys.argv[1:]

    # Map command line parameters to new optimizer parameters if needed
    # If no args, run with defaults
    if not args:
        print("Usage: python md_minifier.py <caminho_orig> [caminho_dest]")
        return

    src = args[0]
    dst = args[1] if len(args) > 1 else (src + "_mimificado")

    cmd = [
        sys.executable,
        optimizer_path,
        "--src", src,
        "--dst", dst,
        "--profile", "markdown",
        "--dictionary-scope", "file",
        "--report-path", os.path.join(dst, "benchmark_report")
    ]

    print("Delegating minification to token_optimizer.py:")
    print(f"  Source: {src}")
    print(f"  Dest:   {dst}")

    # Run optimizer process
    result = subprocess.run(cmd)
    sys.exit(result.returncode)

if __name__ == "__main__":
    main()
