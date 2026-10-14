import argparse
import time
import requests


def transcribe(file_path: str, server_url: str, audio_ctx: int) -> tuple[str, float]:
    with open(file_path, "rb") as f:
        files = {"file": f}
        data = {"audio_ctx": str(audio_ctx)}
        start = time.perf_counter()
        resp = requests.post(server_url, files=files, data=data, timeout=30)
        elapsed = time.perf_counter() - start
    resp.raise_for_status()
    text = resp.json().get("text", "").strip()
    return text, elapsed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Transcribe a WAV file via a running whisper-server instance."
    )
    parser.add_argument("file", help="Path to WAV file")
    parser.add_argument("--url", default="http://127.0.0.1:8080/inference")
    parser.add_argument("--audio-ctx", type=int, default=512)
    parser.add_argument("--runs", type=int, default=3, help="Number of repeated requests")
    args = parser.parse_args()

    for i in range(1, args.runs + 1):
        text, elapsed = transcribe(args.file, args.url, args.audio_ctx)
        print(f"Run {i}: {elapsed:.3f}s -> {text!r}")
