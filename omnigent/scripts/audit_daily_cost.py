#!/usr/bin/env python3
"""Audit Omnigent's daily cost rollup against what the provider actually billed.

``GET /v1/usage`` reports spend from ``user_daily_cost``, which is fed by the
per-turn cost deltas each harness reports. That figure can drift away from the
provider's invoice — historically because a native harness reported a
*per-process* cumulative total that reset on every relaunch, so the monotonic
clamp froze each session at its best single-process total while the provider
kept billing every process.

This tool makes that drift visible. For each settled UTC day it compares:

- **tab** — the number the usage tab shows (``user_daily_cost``).
- **priced** — the provider's *metered tokens* for this deployment's own
  sessions, priced with Omnigent's own catalog rates (so a rate mismatch and a
  token-accounting mismatch stay distinguishable).
- **account** — the provider's rated total for the whole account, including
  traffic Omnigent never saw (other CLIs, other machines, other API keys).

Only ``tab`` vs ``priced`` is a fair pass/fail: a user's Omnigent sessions
cannot account for traffic that never ran through Omnigent. The pass bar is a
per-day deviation of at most ``--tolerance`` (default 5%).

Usage::

    python -m omnigent.scripts.audit_daily_cost --since 2026-10-01 --until 2026-10-21 \\
        --server https://omni.mdatla.us --fireworks-account my-account

The Fireworks API key is read from ``$FIREWORKS_API_KEY`` by default (see
``docs`` for the provider's ``billingUsage`` endpoint). Provider data is
aggregated into daily buckets and lags live traffic by up to a day, so the most
recent ``--settle-days`` (default 2) are reported but never fail the audit.
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_FIREWORKS_BASE = "https://api.fireworks.ai"
_AUTH_TOKENS_PATH = Path.home() / ".omnigent" / "auth_tokens.json"


def _server_token(server: str) -> str:
    """Read the CLI's bearer token for *server* from the Omnigent auth store."""
    try:
        raw = json.loads(_AUTH_TOKENS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read {_AUTH_TOKENS_PATH}: {exc}") from exc
    entry = raw.get(server) or raw.get(server.rstrip("/"))
    token = entry.get("token") if isinstance(entry, dict) else None
    if not token:
        raise SystemExit(
            f"no stored token for {server!r} in {_AUTH_TOKENS_PATH}; run `omnigent login`"
        )
    return str(token)


def _get_json(url: str, headers: dict[str, str]) -> Any:
    """GET *url* and decode its JSON body."""
    # A real User-Agent: the default ``Python-urllib/3.x`` is rejected with 403
    # by the edge in front of both services.
    request = urllib.request.Request(url, headers={"User-Agent": "omnigent-cost-audit", **headers})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return json.loads(response.read())


def _server_usage(server: str, token: str) -> dict[str, Any]:
    """Fetch the ``/v1/usage`` report (daily rollup + per-session detail)."""
    return _get_json(
        f"{server.rstrip('/')}/v1/usage", {"Authorization": f"Bearer {token}"}
    )


def _server_sessions(server: str, token: str) -> list[dict[str, Any]]:
    """List the caller's sessions, paginating to the end."""
    headers = {"Authorization": f"Bearer {token}"}
    out: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        query = {"limit": "200"}
        if after:
            query["after"] = after
        url = f"{server.rstrip('/')}/v1/sessions?{urllib.parse.urlencode(query)}"
        page = _get_json(url, headers)
        rows = page.get("data") or []
        out.extend(rows)
        if not page.get("has_more") or not rows:
            return out
        after = page.get("last_id")


def _fireworks_day_tokens(
    *, account: str, key: str, day: str
) -> list[dict[str, Any]]:
    """Return one day's serverless usage rows grouped by session and model."""
    query = urllib.parse.urlencode(
        {
            "startTime": f"{day}T00:00:00Z",
            "endTime": f"{day}T23:59:59Z",
            "usageType": "SERVERLESS",
            "groupBy": ["session_id", "model_name"],
        },
        doseq=True,
    )
    url = f"{_FIREWORKS_BASE}/v1/accounts/{account}/billingUsage?{query}"
    body = _get_json(url, {"Authorization": f"Bearer {key}"})
    return list(body.get("serverlessCosts") or [])


def _price(model: str, usage: dict[str, int]) -> float | None:
    """Price one usage row with Omnigent's catalog rates, or ``None`` if unpriced."""
    from omnigent.llms.context_window import compute_llm_cost, fetch_model_pricing

    pricing = fetch_model_pricing(model)
    if pricing is None:
        return None
    return compute_llm_cost(usage, pricing)


def _settled_days(since: str, until: str, settle_days: int) -> list[str]:
    """List the ``since``..``until`` UTC days that are old enough to compare."""
    start = _dt.date.fromisoformat(since)
    end = _dt.date.fromisoformat(until)
    cutoff = _dt.datetime.now(_dt.UTC).date() - _dt.timedelta(days=settle_days)
    days: list[str] = []
    day = start
    while day <= end:
        days.append(day.isoformat())
        day += _dt.timedelta(days=1)
    return days, cutoff  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    """Run the audit; return a process exit status (1 when a day fails)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="first UTC day, YYYY-MM-DD")
    parser.add_argument("--until", required=True, help="last UTC day, YYYY-MM-DD")
    parser.add_argument("--server", default="https://omni.mdatla.us")
    parser.add_argument("--token", default=None, help="override the stored bearer token")
    parser.add_argument("--fireworks-account", default=os.environ.get("FIREWORKS_ACCOUNT"))
    parser.add_argument(
        "--fireworks-key",
        default=os.environ.get("FIREWORKS_API_KEY"),
        help="Fireworks API key (default: $FIREWORKS_API_KEY)",
    )
    parser.add_argument("--tolerance", type=float, default=0.05)
    parser.add_argument(
        "--settle-days",
        type=int,
        default=2,
        help="days of recent traffic to report but not judge (provider lag)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args(argv)

    if not args.fireworks_account or not args.fireworks_key:
        raise SystemExit(
            "set --fireworks-account and FIREWORKS_API_KEY (or --fireworks-key)"
        )

    token = args.token or _server_token(args.server)
    report = _server_usage(args.server, token)
    sessions = _server_sessions(args.server, token)
    ours = {
        row["external_session_id"]
        for row in sessions
        if row.get("external_session_id")
    }
    tab_daily = {row["day"]: float(row["cost_usd"]) for row in report["daily_costs"]}

    days, cutoff = _settled_days(args.since, args.until, args.settle_days)
    rows: list[dict[str, Any]] = []
    unpriced: collections.Counter[str] = collections.Counter()
    failures = 0
    for day in days:
        priced = 0.0
        account_total = 0.0
        for row in _fireworks_day_tokens(
            account=args.fireworks_account, key=args.fireworks_key, day=day
        ):
            group = row.get("group") or {}
            model = str(group.get("model_name") or "")
            prompt = int(row.get("promptTokens") or 0)
            cached = int(row.get("cachedPromptTokens") or 0)
            output = int(row.get("completionTokens") or 0)
            cost = _price(
                model,
                {
                    "input_tokens": prompt - cached,
                    "output_tokens": output,
                    "cache_read_input_tokens": cached,
                },
            )
            if cost is None:
                unpriced[model] += prompt + output
                continue
            account_total += cost
            if group.get("session_id") in ours:
                priced += cost
        tab = tab_daily.get(day, 0.0)
        settled = _dt.date.fromisoformat(day) < cutoff
        deviation = (tab - priced) / priced if priced > 0 else (0.0 if tab == 0 else 1.0)
        failed = settled and abs(deviation) > args.tolerance
        if failed:
            failures += 1
        rows.append(
            {
                "day": day,
                "tab_usd": round(tab, 6),
                "priced_usd": round(priced, 6),
                "account_usd": round(account_total, 6),
                "deviation": None if priced == 0 else round(deviation, 4),
                "settled": settled,
                "within_tolerance": (not settled) or not failed,
            }
        )

    if args.json:
        json.dump(
            {"rows": rows, "unpriced_models": dict(unpriced), "tolerance": args.tolerance},
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        print(
            f"{'day':12}{'tab':>10}{'priced':>10}{'account':>10}{'delta':>9}  status"
        )
        for row in rows:
            delta = "—" if row["deviation"] is None else f"{row['deviation'] * 100:+.1f}%"
            status = "ok" if row["settled"] else "unsettled (provider lag)"
            if row["settled"] and not row["within_tolerance"]:
                status = "FAIL"
            print(
                f"{row['day']:12}{row['tab_usd']:10.4f}{row['priced_usd']:10.4f}"
                f"{row['account_usd']:10.4f}{delta:>9}  {status}"
            )
        if unpriced:
            detail = ", ".join(f"{model} ({tokens} tokens)" for model, tokens in unpriced.items())
            print(f"\nunpriced models (excluded from `priced`): {detail}")
        print(
            f"\nbar: per-day |tab - priced| <= {args.tolerance * 100:.0f}% on settled days; "
            f"{failures} failing day(s)"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
