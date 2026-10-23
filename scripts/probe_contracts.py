"""Small live verification of the production-generated strict schema (P1.2)."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator
from safebank_contracts.plan import PlanBody
from safebank_contracts.schema import from_strict_plan, plan_tool

from scripts.deepseek_probe import load_key


async def main() -> None:
    tool = plan_tool()
    schema = tool["function"]["parameters"]
    root = Path("docs/acceptance/p1-contracts-live")
    root.mkdir(exist_ok=True)
    (root / "schema.json").write_text(json.dumps(tool, ensure_ascii=False, indent=2))
    results = []
    key = load_key()
    async with httpx.AsyncClient(timeout=60, headers={"Authorization": f"Bearer {key}"}) as client:
        for prompt in (
            "给王五转800元，账户acc_a收款人pye_b，memo为空",
            "冻结crd_a卡",
            "把crd_a的单笔限额降低到1000分，日限额不变",
        ):
            body = dict(
                model="deepseek-v4-pro",
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                        + "。使用一步计划，参数来源一律kind=read,ref=src_test,pointer=/value，"
                        "不使用utterance来源；中文解释。",
                    }
                ],
                thinking={"type": "disabled"},
                temperature=0,
                max_tokens=4096,
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": "submit_action_plan"}},
            )
            response = await client.post(
                "https://api.deepseek.com/beta/chat/completions", json=body
            )
            data = response.json()
            record = {
                "at_wall": datetime.now(UTC).isoformat(),
                "http_status": response.status_code,
                "response": data,
                "valid": False,
            }
            if response.status_code == 200:
                args = json.loads(
                    data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
                )
                Draft202012Validator(schema).validate(args)
                PlanBody.model_validate(from_strict_plan(args))
                record["valid"] = True
            results.append(record)
            (root / "results.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2).replace(key, "[REDACTED]")
            )
            print({"http_status": response.status_code, "valid": record["valid"]}, flush=True)
            if not record["valid"]:
                raise SystemExit("Production schema was rejected; inspect sanitized evidence")


if __name__ == "__main__":
    asyncio.run(main())
