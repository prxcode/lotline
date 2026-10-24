import os
import sys
import json
import argparse
from dotenv import load_dotenv

load_dotenv()


def serve():
    import uvicorn
    # 0.0.0.0 so the API is reachable from outside a container
    uvicorn.run("api.main:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))


def main():
    parser = argparse.ArgumentParser(description="Lotline: food bank recall tracing. With no options, starts the web app and API.")
    parser.add_argument("--input", default=None, help="Run one input file, e.g. input_examples/example_1.json")
    parser.add_argument("--recall", default=None, help="Run one recall by ID, e.g. R-001")
    parser.add_argument("--mode", choices=["sample", "live"], default=None, help="Override SAMPLE_MODE from .env")
    parser.add_argument("--output", default=None, help="Write the full response to this JSON file")
    args = parser.parse_args()

    if not args.input and not args.recall:
        return serve()

    recall_id, sample = args.recall, None if args.mode is None else args.mode == "sample"
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            request = json.load(f)
        recall_id = recall_id or request.get("recall_id")
        if sample is None and "sample_mode" in request.get("options", {}):
            sample = bool(request["options"]["sample_mode"])

    from graph.lotline_graph import run_recall
    from llm.client import LLMError
    from zetaris.client import DataError

    try:
        res = run_recall(recall_id, sample_mode=sample)
    except (LookupError, LLMError, DataError) as exc:
        print(json.dumps({"status": "error", "error": {"type": type(exc).__name__, "message": str(exc)}}), file=sys.stderr)
        sys.exit(1)

    pack = res["output"]
    print(f"run_id  : {res['run_id']}  ({pack['mode']} mode, {pack['model']}, {res['execution_time_seconds']}s)")
    print(f"recall  : {pack['recall_summary']}")
    print(f"summary : {pack['verdict_summary']}")
    for gap in pack.get("data_gaps", []):
        print(f"degraded: {gap}")
    print()

    sections = [("PULL NOW", pack["pull_list"]), ("CHECK BY HAND", pack["human_checks"]), ("RULED OUT", pack["near_misses"])]
    for title, items in sections:
        if items:
            print(f"{title} ({len(items)})")
            for v in items:
                print(f"  {v['item_id']:<8} {v['verdict']:<16} {v.get('location') or '':<10} {v['rationale']}")
            print()

    if pack["agency_notices"]:
        print(f"NOTIFY ({len(pack['agency_notices'])} agencies)")
        for n in pack["agency_notices"]:
            print(f"  {n['agency_name']} <{n['contact_email']}>: {len(n['items'])} shipment(s)")
        print()

    print(f"trace   : {res['log_file']}")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, default=str)
        print(f"written : {args.output}")


if __name__ == "__main__":
    main()
