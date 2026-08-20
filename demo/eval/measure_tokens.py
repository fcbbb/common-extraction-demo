from __future__ import annotations

import argparse
import io
import json
import tokenize
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def count_tokens(text: str) -> tuple[int, str]:
    try:
        import tiktoken

        encoding = tiktoken.encoding_for_model("gpt-4")
        return len(encoding.encode(text)), "tiktoken:gpt-4"
    except Exception:
        tokens = [
            tok.string
            for tok in tokenize.generate_tokens(io.StringIO(text).readline)
            if tok.type
            not in {
                tokenize.ENCODING,
                tokenize.ENDMARKER,
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
            }
        ]
        return len(tokens), "python-tokenize"


def concat_files(paths: list[Path]) -> str:
    return "\n\n".join(path.read_text(encoding="utf-8") for path in paths if path.exists())


def measure(
    original_dir: Path,
    result_dir: Path,
    file_ids: list[str] | None = None,
) -> dict[str, object]:
    original_files = sorted(original_dir.glob("file_*.py"))
    refactored_files = sorted((result_dir / "refactored").glob("file_*.py"))
    if file_ids is not None:
        ids = set(file_ids)
        original_files = [f for f in original_files if f.name in ids]
        refactored_files = [f for f in refactored_files if f.name in ids]
    before_text = concat_files(original_files)
    after_text = concat_files([result_dir / "common.py", *refactored_files])
    before_tokens, tokenizer = count_tokens(before_text)
    after_tokens, after_tokenizer = count_tokens(after_text)
    if tokenizer != after_tokenizer:
        raise RuntimeError(f"Tokenizer mismatch: {tokenizer} vs {after_tokenizer}")
    return {
        "tokenizer": tokenizer,
        "tokens_before": before_tokens,
        "tokens_after": after_tokens,
        "token_ratio_after_before": after_tokens / before_tokens if before_tokens else None,
        "tokens_compression": 1 - (after_tokens / before_tokens) if before_tokens else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure token compression.")
    parser.add_argument("--original-dir", type=Path, required=True)
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = measure(args.original_dir, args.result_dir)
    payload = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
