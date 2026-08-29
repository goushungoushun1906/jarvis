"""JARVIS automated evaluation runner.

Usage:
    python -m evals.run
    python -m evals.run --base http://127.0.0.1:18200
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field

import httpx

from .cases import ALL_CASES, EvalResult

logger = logging.getLogger("jarvis.evals")


@dataclass
class EvalReport:
    """Aggregated report from one evaluation run."""

    results: list[EvalResult] = field(default_factory=list)
    total_ms: float = 0.0

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def pass_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.passed / self.total

    def print_report(self) -> None:
        print("\n" + "=" * 60)
        print("JARVIS Evals Report")
        print("=" * 60)
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            print(f"[{status}] {r.name:20s} {r.duration_ms:8.1f}ms  {r.message}")
        print("-" * 60)
        print(
            f"Total: {self.total}  Passed: {self.passed}  Failed: {self.failed}  "
            f"Rate: {self.pass_rate:.0%}  Time: {self.total_ms:.1f}ms"
        )
        print("=" * 60)


class EvalRunner:
    """Discover and execute eval cases against a running JARVIS backend."""

    def __init__(self, base_url: str = "http://127.0.0.1:18200"):
        self.base_url = base_url.rstrip("/")

    async def run(self, case_filter: str | None = None) -> EvalReport:
        """Run all (or filtered) eval cases and return the report."""
        report = EvalReport()
        cases = ALL_CASES
        if case_filter:
            cases = [c for c in cases if case_filter.lower() in c.__name__.lower()]

        async with httpx.AsyncClient() as client:
            start = time.perf_counter()
            for case in cases:
                try:
                    result = await case(client, self.base_url)
                except Exception as exc:
                    result = EvalResult(
                        name=case.__name__,
                        passed=False,
                        message=f"Unhandled exception: {exc}",
                    )
                report.results.append(result)
            report.total_ms = (time.perf_counter() - start) * 1000

        return report


async def main() -> int:
    parser = argparse.ArgumentParser(description="JARVIS automated evaluation runner")
    parser.add_argument(
        "--base",
        default="http://127.0.0.1:18200",
        help="Base URL of the JARVIS backend",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="Only run cases whose name contains this substring",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    runner = EvalRunner(base_url=args.base)
    report = await runner.run(case_filter=args.filter)
    report.print_report()
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
