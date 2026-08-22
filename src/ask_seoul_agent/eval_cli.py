"""Command line entrypoint for reproducible deterministic and opt-in live evaluations."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

from . import __version__
from .evaluation import EvalReport, load_eval_manifest
from .providers.base import LLMProvider


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "live" and os.getenv("AGENT_EVAL_LIVE") != "1":
        print(
            "live evaluation is disabled; set AGENT_EVAL_LIVE=1 to authorize external calls",
            file=sys.stderr,
        )
        return 2

    manifest = load_eval_manifest(args.manifest)
    if args.mode == "deterministic":
        from .eval_runtime import run_deterministic_suite

        report = asyncio.run(
            run_deterministic_suite(manifest, application_version=args.application_version)
        )
    else:
        from .live_eval import run_live_suite, select_live_cases

        case_ids = set(args.case_id) if args.case_id else None
        try:
            select_live_cases(manifest, case_ids)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        provider_name = args.provider
        api_key_variable = {
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
        }[provider_name]
        api_key = os.getenv(api_key_variable)
        if not api_key:
            print(f"{api_key_variable} is not configured", file=sys.stderr)
            return 2
        model = args.model or _default_model(provider_name)
        provider: LLMProvider
        if provider_name == "anthropic":
            from .providers.anthropic import AnthropicMessagesProvider

            provider = AnthropicMessagesProvider(api_key=api_key, model=model)
        else:
            from .providers.gemini import GeminiGenerateContentProvider

            provider = GeminiGenerateContentProvider(api_key=api_key, model=model)

        async def run_and_close() -> EvalReport:
            try:
                return await run_live_suite(
                    manifest,
                    provider=provider,
                    provider_name=provider_name,
                    model=model,
                    application_version=args.application_version,
                    case_ids=case_ids,
                    inter_case_delay_s=args.inter_case_delay_seconds,
                )
            finally:
                await provider.aclose()

        report = asyncio.run(run_and_close())

    _write_report_atomically(args.output, report)
    return 0 if report.summary.failed == 0 and report.summary.inconclusive_infra == 0 else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("deterministic", "live"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--application-version", default=__version__)
    parser.add_argument("--provider", choices=("anthropic", "gemini"), default="anthropic")
    parser.add_argument("--model")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument(
        "--inter-case-delay-seconds",
        type=_bounded_delay,
        default=0.0,
        help="Pause between live cases to respect provider request-per-minute limits.",
    )
    return parser


def _default_model(provider_name: str) -> str:
    if provider_name == "gemini":
        from .providers.gemini import DEFAULT_MODEL

        return DEFAULT_MODEL
    from .providers.anthropic import DEFAULT_MODEL

    return DEFAULT_MODEL


def _bounded_delay(value: str) -> float:
    try:
        delay = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("delay must be a number") from exc
    if not 0 <= delay <= 300:
        raise argparse.ArgumentTypeError("delay must be between 0 and 300 seconds")
    return delay


def _write_report_atomically(path: Path, report: EvalReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            report.model_dump_json(indent=2, exclude_none=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
