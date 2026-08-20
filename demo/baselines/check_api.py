from __future__ import annotations

import argparse
from time import perf_counter

from demo.baselines.llm_client import DeepSeekClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a tiny DeepSeek request to verify API/model connectivity.")
    parser.add_argument("--api-timeout-sec", type=float, default=30.0)
    parser.add_argument("--max-output-tokens", type=int, default=32)
    args = parser.parse_args()

    client = DeepSeekClient(timeout_sec=args.api_timeout_sec, max_tokens=args.max_output_tokens)
    print(f"base_url={client.base_url}")
    print(f"model={client.model}")
    print(f"timeout_sec={client.timeout_sec}")
    print(f"max_tokens={client.max_tokens}")
    start = perf_counter()
    response = client.chat(
        "Return valid JSON only.",
        'Return exactly {"ok": true}.',
    )
    elapsed = perf_counter() - start
    print(f"latency_sec={elapsed:.2f}")
    print(response.content)


if __name__ == "__main__":
    main()
