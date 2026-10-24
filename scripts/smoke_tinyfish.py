
# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

import json
from datetime import datetime
from pathlib import Path


OUTPUT_PATH = Path(__file__).resolve().parent.parent / "out" / "smoke_test_1.md"


def _on_event_factory(label: str):
    def on_event(message: str):
        print(f"  [{label}] {message}")
    return on_event


def _format_result(label: str, r) -> str:
    status = "OK" if r.ok else "FAILED"

    lines = [
        f"## {label}",
        "",
        f"- **Status:** {status}",
        f"- **Elapsed:** {r.elapsed_s:.1f}s",
        f"- **Run ID:** {r.run_id or 'N/A'}",
        "",
    ]

    if r.error:
        lines.extend([
            "### Error",
            "",
            "```text",
            str(r.error),
            "```",
            "",
        ])

    if r.parsed_result is not None:
        lines.extend([
            "### Parsed result",
            "",
            "```json",
            json.dumps(r.parsed_result, indent=2, ensure_ascii=False),
            "```",
            "",
        ])
    elif r.raw_result is not None:
        lines.extend([
            "### Raw result (no schema match)",
            "",
            "```text",
            str(r.raw_result),
            "```",
            "",
        ])
    else:
        lines.extend(["No result returned.", ""])

    return "\n".join(lines)


def _print_result(label: str, r) -> None:
    status = "OK" if r.ok else "FAILED"
    print(
        f"\n--- {label} [{status}] "
        f"elapsed={r.elapsed_s:.1f}s run_id={r.run_id} ---"
    )

    if r.error:
        print(f"  error: {r.error}")

    if r.parsed_result is not None:
        print(json.dumps(r.parsed_result, indent=2, ensure_ascii=False)[:2000])
    elif r.raw_result is not None:
        print(f"  raw_result (no schema match): {str(r.raw_result)[:500]}")
    else:
        print("  no result returned")


async def main():
    report_sections = [
        "# TinyFish Smoke Test",
        "",
        f"- **Run at:** {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- **Output:** `{OUTPUT_PATH}`",
        "",
        "This report contains the results of two real TinyFish calls.",
        "",
    ]

    print("=" * 60)
    print("Smoke test (a): GitHub good-first-issue search")
    print("=" * 60)

    result_a = await run_tinyfish(
        goal=SEARCH_GOAL,
        url=SEARCH_URL,
        output_schema=SEARCH_SCHEMA,
        on_event=_on_event_factory("search"),
    )
    _print_result("(a) search", result_a)
    report_sections.append(_format_result("(a) GitHub good-first-issue search", result_a))

    print()
    print("=" * 60)
    print("Smoke test (b): DSPy #9074 investigation")
    print("=" * 60)

    result_b = await run_tinyfish(
        goal=ISSUE_GOAL,
        url=ISSUE_URL,
        output_schema=ISSUE_SCHEMA,
        on_event=_on_event_factory("dspy"),
    )
    _print_result("(b) dspy #9074", result_b)
    report_sections.append(_format_result("(b) DSPy #9074 investigation", result_b))

    # Save the complete report, even if one of the calls failed.
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        "\n".join(report_sections) + "\n",
        encoding="utf-8",
    )

    print(f"\nFull report saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
