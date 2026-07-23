import json
import sys
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest_a")
    parser.add_argument("manifest_b")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    with open(args.manifest_a, 'r', encoding='utf-8') as f:
        a = json.load(f)
    with open(args.manifest_b, 'r', encoding='utf-8') as f:
        b = json.load(f)

    hash_a = a.get("tree_sha256")
    hash_b = b.get("tree_sha256")

    print(f"Manifest A: {args.manifest_a}")
    print(f"  Tree SHA256: {hash_a}")
    print(f"Manifest B: {args.manifest_b}")
    print(f"  Tree SHA256: {hash_b}")

    if hash_a != hash_b:
        print("WARNING: Manifests differ!")
        files_a = {f["path"]: f["sha256"] for f in a.get("files", [])}
        files_b = {f["path"]: f["sha256"] for f in b.get("files", [])}
        
        all_paths = set(files_a.keys()) | set(files_b.keys())
        for path in sorted(all_paths):
            sha_a = files_a.get(path)
            sha_b = files_b.get(path)
            if sha_a != sha_b:
                print(f"  Path: {path}")
                print(f"    A: {sha_a}")
                print(f"    B: {sha_b}")
        if args.strict:
            sys.exit(1)
    else:
        print("SUCCESS: Manifests are identical.")

if __name__ == "__main__":
    main()
