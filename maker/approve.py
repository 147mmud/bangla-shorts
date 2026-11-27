#!/usr/bin/env python3
"""Make NEW pictures for the scenes you asked to redo in Shorts Studio.

Shorts Studio writes  approve/<name>/redo.json  like  {"items": [{"k": 2, "hint": "only a mosque"}]}
(k = scene number, 1 = first). Each listed picture is made again (new seed, your hint added),
then redo.json is removed so Shorts Studio shows the new pictures for your OK.
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_short import ROOT, log  # noqa: E402
from run_queue import save_progress  # noqa: E402


def main():
    import ai_tools
    jobs = sorted((ROOT / "approve").glob("*/redo.json")) if (ROOT / "approve").is_dir() else []
    for rj in jobs:
        d = rj.parent
        try:
            req = json.loads(rj.read_text(encoding="utf-8") or "{}")
            plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log("Could not read", rj, e)
            rj.unlink(missing_ok=True)
            continue
        for it in req.get("items", []):
            try:
                k = int(it.get("k", 0)) - 1
            except (TypeError, ValueError):
                continue
            if not 0 <= k < len(plan["files"]):
                continue
            prompt = plan["prompts"][k]
            hint = str(it.get("hint", "")).strip()[:300]
            if hint:
                try:  # your note (maybe in Bangla) -> English for the picture AI
                    prompt = ai_tools.llm([{"role": "user", "content":
                        "Rewrite this AI image prompt following the change request. " + ai_tools._channel_rules({"about": "islam"} if "no humans" in plan.get("style", "") else {}) +
                        "Reply with the new English prompt only (max 45 words).\n\nPrompt: " + prompt +
                        "\nChange request: " + hint}], temperature=0.4).strip().strip('"') or prompt
                except Exception as e:  # noqa: BLE001
                    log("Hint not used:", str(e)[:120])
            f = d / plan["files"][k]
            log(f"New picture for scene {k + 1}: {prompt[:100]}")
            if ai_tools.ai_image(prompt, f, seed=random.randint(1, 99999), style=plan.get("style", "")):
                plan["prompts"][k] = prompt
        (d / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        rj.unlink(missing_ok=True)
        save_progress(f"New pictures for {d.name} [skip ci]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
