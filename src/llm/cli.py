"""Historical LLM benchmark entry point (S8). Kept as a thin alias so existing commands keep
working; the single implementation lives in `src.llm.benchmark` (ADR 0026):

    python -m src.llm.cli --provider openai  ==  python -m src.llm.benchmark --track historical

Real calls need ALLOW_REAL_LLM_CALLS=true and stay inside the configured budget.
"""

import sys

from .benchmark import main as _benchmark_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--track" not in args:
        args = ["--track", "historical", *args]
    return _benchmark_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
