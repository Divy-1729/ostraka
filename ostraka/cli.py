import argparse
import os
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Ostraka · read, understand, present")
    parser.add_argument("--port", type=int, default=int(os.getenv("OSTRAKA_PORT", "8000")))
    args = parser.parse_args()
    print(f"\n  Ostraka → http://localhost:{args.port}\n  Press Ctrl+C to stop. Your library stays on this computer.\n")
    uvicorn.run("ostraka.app:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
