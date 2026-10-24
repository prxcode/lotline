import os
import re
import sys
import csv
import argparse
from collections import Counter, defaultdict
from dotenv import load_dotenv

load_dotenv()

ACTION = {"pull_and_notify": "AFFECTED", "human_verify": "NEEDS_CHECK", "monitor": "NOT_AFFECTED", "clear": "NOT_AFFECTED"}
LABELS = ("AFFECTED", "NEEDS_CHECK", "NOT_AFFECTED")


def _read_key(data_dir: str):
    items, dists, extra = defaultdict(dict), defaultdict(set), {}
    with open(f"{data_dir}/answer_key.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rid, iid = row["recall_id"], row["item_id"]
            if iid.startswith("DIST-"):
                dists[rid].add(iid)
                continue
            # verdict per item; an older format used expected_action
            items[rid][iid] = row.get("verdict") or ACTION[row["expected_action"]]
            if row.get("verdict"):
                agencies = set(re.findall(r"[A-Z]+-\d+", row.get("agencies_that_received_it") or ""))
                still = row.get("quantity_still_in_warehouse") or ""
                extra[(rid, iid)] = {"agencies": agencies, "still": float(still) if still.strip() else None}
    return items, dists, extra


def main():
    parser = argparse.ArgumentParser(description="Score Lotline against a dataset's answer_key.csv")
    parser.add_argument("--mode", choices=["sample", "live"], default="sample")
    parser.add_argument("--data", default=None, help="Data folder (default: DATA_DIR or data)")
    parser.add_argument("--recalls", default=None, help="Only these recalls: R-006,R-007, a range R-006..R-010, or a prefix RC-")
    parser.add_argument("--no-aliases", action="store_true",
                        help="Simulate a model that suggests no brand spellings (tests the brand resolver alone)")
    args = parser.parse_args()
    if args.data:
        os.environ["DATA_DIR"] = args.data
    data_dir = os.getenv("DATA_DIR", "data")

    if args.no_aliases:
        import llm.client as llm
        llm._SAMPLE["triage"] = lambda p: {"brand_aliases": []}

    from graph.lotline_graph import run_recall

    items, dists, extra = _read_key(data_dir)
    wanted = sorted(items)
    if args.recalls:
        if args.recalls.endswith("-") and "," not in args.recalls:
            wanted = [r for r in wanted if r.startswith(args.recalls)]
        elif ".." in args.recalls:
            lo, hi = args.recalls.split("..")
            wanted = [r for r in wanted if lo <= r <= hi]
        else:
            wanted = [r for r in wanted if r in args.recalls.split(",")]

    confusion = Counter()
    unsafe = escalations = dist_total = dist_found = 0
    agency_ok = agency_n = still_ok = still_n = 0
    print(f"{data_dir}, {args.mode} mode\n{'recall':<12} {'items':>7} {'shipments':>10}  issues")
    for rid in wanted:
        pack = run_recall(rid, sample_mode=args.mode == "sample")["output"]
        verdicts = pack["pull_list"] + pack["human_checks"] + pack["near_misses"]
        got = {v["item_id"]: ACTION[v["action"]] for v in verdicts}
        by_item = {v["item_id"]: v for v in verdicts}
        escalated = {v["item_id"] for v in verdicts if v.get("source") == "rules+model escalation"}
        issues, ok = [], 0
        for iid, want in items[rid].items():
            have = got.get(iid, "NOT_AFFECTED")
            confusion[(want, have)] += 1
            if have == want:
                ok += 1
            else:
                note = " (reviewer escalation)" if iid in escalated else ""
                why = by_item.get(iid, {}).get("rationale", "not found by the search")
                issues.append(f"{iid} expected {want} got {have}{note}: {why[:110]}")
                escalations += bool(note) and want == "NOT_AFFECTED" and have == "NEEDS_CHECK"
                # an affected or uncertain item marked safe is the failure that hurts people
                unsafe += want in ("AFFECTED", "NEEDS_CHECK") and have == "NOT_AFFECTED"
            info = extra.get((rid, iid))
            if info and want != "NOT_AFFECTED" and have == want:
                sent = {d["agency_id"] for d in pack["distributed_items"] if d["item_id"] == iid}
                agency_n += 1
                agency_ok += sent == info["agencies"]
                if sent != info["agencies"]:
                    issues.append(f"{iid} agencies {sorted(sent)} expected {sorted(info['agencies'])}")
                if info["still"] is not None:
                    still_n += 1
                    on_hand = by_item[iid].get("quantity")
                    still_ok += on_hand is not None and abs(on_hand - info["still"]) < 0.01
        found = {d["dist_id"] for d in pack["distributed_items"]}
        lost = dists[rid] - found
        dist_total += len(dists[rid])
        dist_found += len(dists[rid]) - len(lost)
        issues += [f"{d} shipment not traced" for d in sorted(lost)]
        shipments = f"{len(dists[rid]) - len(lost)}/{len(dists[rid])}" if dists[rid] else "-"
        print(f"{rid:<12} {ok:>3}/{len(items[rid]):<3} {shipments:>10}  {'none' if not issues else ''}")
        for i in issues:
            print(f"{'':>25}{i}")

    total = sum(confusion.values())
    correct = sum(n for (w, h), n in confusion.items() if w == h)
    print(f"\nitem accuracy   : {correct}/{total} ({correct / total:.0%})")
    print("expected -> got : " + ("; ".join(f"{w}->{h} {n}" for (w, h), n in sorted(confusion.items()) if w != h) or "all correct"))
    if dist_total:
        print(f"shipments traced: {dist_found}/{dist_total}")
    if agency_n:
        print(f"agencies right  : {agency_ok}/{agency_n} flagged items")
    if still_n:
        print(f"on hand right   : {still_ok}/{still_n} flagged items")
    print(f"unsafe misses   : {unsafe} (affected or uncertain items marked safe)")
    if escalations:
        print(f"extra hand checks the model asked for (safe direction): {escalations}")
    sys.exit(1 if unsafe or dist_found < dist_total else 0)


if __name__ == "__main__":
    main()
