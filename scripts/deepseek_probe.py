"""P0 real API probes; records JSON bodies/usage, never headers or credentials."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator


def obj(fields: dict) -> dict:
    return {
        "type": "object",
        "properties": fields,
        "required": list(fields),
        "additionalProperties": False,
    }


def string() -> dict:
    return {"type": "string"}


def plan_schema() -> dict:
    text = string()
    integer = {"type": "integer"}
    boolean = {"type": "boolean"}
    entity = {
        "anyOf": [
            obj({"kind": {"enum": ["id"]}, "id": text}),
            obj({"kind": {"enum": ["ref"]}, "ref": text}),
            obj({"kind": {"enum": ["none"]}}),
        ]
    }
    leaf = {
        "anyOf": [
            obj(
                {
                    "kind": {"enum": ["utterance"]},
                    "ref": text,
                    "span": {"type": "array", "items": integer},
                }
            ),
            obj({"kind": {"enum": ["read"]}, "ref": text, "pointer": text}),
            obj({"kind": {"enum": ["step"]}, "ref": text}),
        ]
    }
    source = {
        "anyOf": [
            *leaf["anyOf"],
            obj({"kind": {"enum": ["hint"]}, "ref": text}),
            obj(
                {
                    "kind": {"enum": ["derived"]},
                    "template": {"enum": ["cancelled_monthly_fees", "available_less_7d_charges"]},
                    "inputs": {"type": "array", "items": leaf},
                }
            ),
        ]
    }
    fields = {
        "transfer": dict(
            from_account_id=text,
            payee_id=entity,
            to_account_id=entity,
            amount_cents=integer,
            memo=text,
        ),
        "add_payee": dict(name=text, account_no=text, bank_name=text, phone=text, alias=text),
        "schedule_transfer": dict(
            from_account_id=text,
            payee_id=entity,
            to_account_id=entity,
            amount_cents=integer,
            period={"enum": ["ONCE", "WEEKLY", "MONTHLY"]},
            first_run_at=text,
            end_at=text,
        ),
        "cancel_scheduled": dict(instruction_id=text),
        "create_split_request": dict(
            account_id=text,
            total_cents=integer,
            participant_payee_ids={"type": "array", "items": text},
            include_self=boolean,
            memo=text,
        ),
        "update_card_limits": dict(
            card_id=text, single_limit_cents=integer, daily_limit_cents=integer
        ),
        "update_card_controls": {
            "card_id": text,
            **{
                k: {"enum": ["KEEP", "ON", "OFF"]}
                for k in ("online", "overseas", "atm", "quickpass")
            },
        },
        "freeze_card": dict(card_id=text),
        "unfreeze_card": dict(card_id=text),
        "report_card_lost": dict(card_id=text, replace=boolean),
        "cancel_subscription": dict(instruction_id=text),
        "subscribe_product": dict(account_id=text, product_id=text, amount_cents=integer),
        "redeem_product": dict(account_id=text, product_id=text, units=text),
    }
    variants = [
        obj(
            {
                "step_id": text,
                "tool": {"enum": [name]},
                "args": obj(args),
                "depends_on": {"type": "array", "items": text},
                "arg_sources": {
                    "type": "array",
                    "items": obj({"param": {"enum": list(args)}, "source": source}),
                },
            }
        )
        for name, args in fields.items()
    ]
    return obj(
        {
            "intents": {"type": "array", "items": text},
            "steps": {"type": "array", "items": {"anyOf": variants}},
            "rationale": text,
        }
    )


def experiments() -> list[tuple[str, dict, str, bool, str]]:
    understanding = obj(
        {
            "intents": {"type": "array", "items": string()},
            "slots": {
                "type": "array",
                "items": obj(
                    {
                        "intent_index": {"type": "integer"},
                        "name": {"enum": ["payee", "amount_cents"]},
                        "value": string(),
                        "span": {"type": "array", "items": {"type": "integer"}},
                    }
                ),
            },
            "missing": {"type": "array", "items": string()},
            "needs_write": {"type": "boolean"},
            "ambiguous_payees": {"type": "array", "items": string()},
        }
    )
    plan_prompt = "用户要求给王五转800元。可信账户acc_zs_chk，收款人pye_zs_ww。用transfer一步计划；实体用kind=id，空目标kind=none，memo=__NULL__。金额80000分。来源utt_test的金额字符区间[6,9]，read来源src_test。不要新增收款人。"  # noqa: E501
    rows = []
    for prompt in ("给王五转800元", "查我的余额", "把卡冻结"):
        rows.append(("E1-understand", understanding, prompt, False, "report_understanding"))
    for prompt in ("查余额不查曲线", "查近7天余额曲线", "查近30天余额曲线"):
        rows.append(
            ("E1-gather", obj({"history_days": {"type": "integer"}}), prompt, False, "get_accounts")
        )
    rows += [
        ("E2", plan_schema(), plan_prompt, True, "submit_action_plan"),
        (
            "E3",
            obj({"memo": {"anyOf": [string(), {"type": "null"}]}}),
            "memo为null",
            False,
            "test_nullable",
        ),
        (
            "E4",
            {
                **obj({"value": {"$ref": "#/$def/Value"}}),
                "$def": {"Value": obj({"amount_cents": {"type": "integer"}})},
            },
            "amount_cents为80000",
            False,
            "test_ref",
        ),
        ("E5", plan_schema(), plan_prompt, False, "submit_action_plan"),
    ]

    def normalize(node):
        if isinstance(node, dict):
            if "enum" in node:
                node["type"] = "string"
            for value in node.values():
                normalize(value)
        elif isinstance(node, list):
            for value in node:
                normalize(value)

    for _, schema, *_ in rows:
        normalize(schema)
    return rows


def load_key() -> str:
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if not key and Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY is missing; no API calls made")
    return key


async def run(repeats: int, concurrency: int, smoke: bool, only: str | None = None) -> Path:
    key = load_key()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = Path("docs/acceptance") / f"deepseek-{stamp}"
    root.mkdir(parents=True)
    semaphore = asyncio.Semaphore(concurrency)
    rows = experiments()[:1] if smoke else experiments()
    (root / "schemas.json").write_text(
        json.dumps({name: schema for name, schema, *_ in rows}, ensure_ascii=False, indent=2)
    )
    if only == "E2-fallback":
        row = next(row for row in experiments() if row[0] == "E2")
        rows = [("E2-fallback", row[1], row[2], False, row[4])]
    results = []
    async with httpx.AsyncClient(timeout=90, headers={"Authorization": f"Bearer {key}"}) as client:

        async def one(row, number):
            name, schema, prompt, thinking, tool = row
            body = {
                "model": "deepseek-v4-pro",
                "messages": [{"role": "user", "content": prompt}],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": tool, "strict": True, "parameters": schema},
                    }
                ],
                "tool_choice": {"type": "function", "function": {"name": tool}},
                "thinking": {"type": "enabled" if thinking else "disabled"},
                "max_tokens": 4096,
            }
            if thinking:
                body["reasoning_effort"] = "low"
            else:
                body["temperature"] = 0
            async with semaphore:
                start = time.perf_counter()
                record = {
                    "experiment": name,
                    "sample": number,
                    "request_hash": hashlib.sha256(
                        json.dumps(body, sort_keys=True).encode()
                    ).hexdigest(),
                    "valid": False,
                }
                try:
                    response = await client.post(
                        "https://api.deepseek.com/beta/chat/completions", json=body
                    )
                    record["http_status"] = response.status_code
                    data = response.json()
                    record["response"] = data
                    if response.status_code == 200:
                        calls = data["choices"][0]["message"].get("tool_calls", [])
                        args = json.loads(calls[0]["function"]["arguments"])
                        Draft202012Validator(schema).validate(args)
                        record["valid"] = len(calls) == 1 and calls[0]["function"]["name"] == tool
                except Exception as exc:
                    record["error_type"] = type(exc).__name__
                record["latency_ms"] = round((time.perf_counter() - start) * 1000)
                # Defense in depth: credential must never appear in output artifacts.
                encoded = json.dumps(record, ensure_ascii=False).replace(key, "[REDACTED]")
                with (root / "results.jsonl").open("a") as stream:
                    stream.write(encoded + "\n")
                results.append(record)
                print(
                    f"{name} {number}: HTTP {record.get('http_status', 'network-error')} "
                    f"valid={record['valid']}",
                    flush=True,
                )

        await asyncio.gather(*(one(row, n) for row in rows for n in range(repeats)))
    summary = {}
    for name in {r[0] for r in rows}:
        samples = [r for r in results if r["experiment"] == name]
        latency = sorted(r["latency_ms"] for r in samples)
        summary[name] = {
            "count": len(samples),
            "valid": sum(r["valid"] for r in samples),
            "http_400": sum(r.get("http_status") == 400 for r in samples),
            "p50_ms": latency[(len(latency) - 1) // 2],
            "p95_ms": latency[min(len(latency) - 1, int(len(latency) * 0.95))],
            "usage": [r.get("response", {}).get("usage") for r in samples],
        }
    (root / "summary.json").write_text(json.dumps(summary, indent=2))
    print(root)
    return root


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--only", choices=["E2-fallback"])
    args = parser.parse_args()
    asyncio.run(run(args.repeats, args.concurrency, args.smoke, args.only))
