import sys
import argparse
from cida.domain.errors import CidaError

from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.hashing import HashService
from cida.infrastructure.json_codec import JsonCodec
from cida.application.decompress_file import FileDecompressorUsecase

def main():
    try:
        parser = argparse.ArgumentParser(description="Production Decompressor for CIDA Sidecars")
        parser.add_argument("--src", required=True, help="Path to compressed file")
        parser.add_argument("--dst", required=True, help="Path to output decompressed file")
        parser.add_argument("--sidecar", help="Path to sidecar file (.cidatkn)")

        args = parser.parse_args()

        file_repo = PhysicalFilesystem()
        json_codec = JsonCodec()
        hash_service = HashService()

        decompressor = FileDecompressorUsecase(file_repo, json_codec, hash_service)
        decompressor.decompress_to_file(args.src, args.dst, args.sidecar)
        print(f"Decompression completed successfully: {args.dst}")
    except CidaError as ce:
        print(f"Error during decompression: {ce}", file=sys.stderr)
        sys.exit(ce.exit_code)
    except Exception as e:
        print(f"Unexpected error during decompression: {e}", file=sys.stderr)
        sys.exit(6)

if __name__ == "__main__":
    main()
