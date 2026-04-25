"""CLI.

Usage:
  python -m backend.ingest dryrun <spec.yaml> <source-file> [--limit N]
  python -m backend.ingest run    <spec.yaml> <source-file> [--limit N] \\
                                   [--db data/better_context.sqlite]
  python -m backend.ingest onboard <source-file> --tenant <name> \\
                                    [--out ingest_specs/<tenant>/<name>.yaml] \\
                                    [--db data/better_context.sqlite]
  python -m backend.ingest promote --tenant <t> --source-pattern <p> --version <n>
  python -m backend.ingest resolve-identity [--db data/better_context.sqlite]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock

from backend.graph.store import GraphStore
from backend.ingest.store import IngestStore
from backend.ingest.ingestor import Ingestor
from backend.ingest.spec import MappingSpec


def _print_report(report, *, store=None) -> None:
    print(f"run_id           = {report.run_id}")
    print(f"records_in       = {report.records_in}")
    print(f"records_out      = {report.records_out}")
    print(f"records_skipped  = {report.records_skipped}")
    print(f"records_dead     = {report.records_dead}")
    print(f"nodes_created    = {report.nodes_created}")
    if store is not None and hasattr(store, "add_node") and hasattr(store.add_node, "call_count"):
        print(f"add_node calls   = {store.add_node.call_count}")
        print(f"add_edge calls   = {store.add_edge.call_count}")
    if report.drift_diffs:
        print(f"drift_diffs      = {report.drift_diffs}")


def _cmd_dryrun(args: argparse.Namespace) -> int:
    spec = MappingSpec.from_yaml(Path(args.spec).read_text(encoding="utf-8"))
    fake_store = MagicMock(spec=GraphStore)
    fake_store.add_source_record.return_value = None
    fake_store.add_node.side_effect = lambda n: n
    fake_store.add_edge.side_effect = lambda e: e
    fake_ingest = MagicMock(spec=IngestStore)
    fake_ingest.already_seen.return_value = False
    ing = Ingestor(fake_store, fake_ingest)
    report = ing.run(spec, args.source, limit=args.limit, dry_run=True)
    _print_report(report, store=fake_store)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    spec = MappingSpec.from_yaml(Path(args.spec).read_text(encoding="utf-8"))
    with GraphStore(args.db) as store:
        ingest_store = IngestStore(store._conn)
        llm = None
        if spec.llm_blocks:
            from backend.ingest.llm import GeminiClient
            llm = GeminiClient(ingest_store)
        ing = Ingestor(store, ingest_store, llm_client=llm)
        report = ing.run(spec, args.source, limit=args.limit, dry_run=False)
        stats = store.stats()
    _print_report(report)
    print(f"graph stats      = {stats['graph']}")
    print(f"trace count      = {stats['traces']['provenance_count']}")
    print(f"raw count        = {stats['raw']['source_record_count']}")
    return 0


def _cmd_onboard(args: argparse.Namespace) -> int:
    from backend.ingest.llm import GeminiClient
    from backend.ingest.onboard import Onboarder

    with GraphStore(args.db) as store:
        ingest_store = IngestStore(store._conn)
        gemini = GeminiClient(ingest_store)
        onboarder = Onboarder(gemini, ingest_store, sample_size=args.sample_size)
        spec = onboarder.draft_spec(args.source, tenant=args.tenant)
    out_path = Path(args.out) if args.out else (
        Path("ingest_specs") / args.tenant / (Path(args.source).stem + ".yaml")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(spec.to_yaml(), encoding="utf-8")
    print(f"draft spec written to {out_path}")
    print(f"  tenant         = {spec.tenant}")
    print(f"  source_pattern = {spec.source.file_pattern}")
    print(f"  spec_version   = {spec.spec_version}")
    print(f"  nodes          = {[n.name + '(' + spec.resolved_node_type(n) + ')' for n in spec.nodes]}")
    print(f"  edges          = {[e.canonical_type for e in spec.edges]}")
    print(f"  llm_blocks     = {[b.name for b in spec.llm_blocks]}")
    print()
    print("Review the YAML, edit if needed, then promote to active:")
    print(
        f"  python -m backend.ingest promote --tenant {args.tenant} "
        f"--source-pattern {spec.source.file_pattern!r} "
        f"--version {spec.spec_version}"
    )
    return 0


def _cmd_promote(args: argparse.Namespace) -> int:
    with GraphStore(args.db) as store:
        ingest_store = IngestStore(store._conn)
        ingest_store.set_spec_status(
            tenant=args.tenant,
            source_pattern=args.source_pattern,
            version=args.version,
            status="active",
        )
    print(f"promoted {args.tenant}/{args.source_pattern} v{args.version} -> active")
    return 0


def _cmd_resolve_identity(args: argparse.Namespace) -> int:
    from backend.ingest.identity import IdentityResolver
    with GraphStore(args.db) as store:
        resolver = IdentityResolver(store)
        report = resolver.resolve()
    print(f"persons_examined  = {report.persons_examined}")
    print(f"clusters_found    = {report.clusters_found}")
    print(f"same_as_created   = {report.same_as_edges_created}")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="backend.ingest")
    sub = p.add_subparsers(dest="cmd", required=True)

    dr = sub.add_parser("dryrun")
    dr.add_argument("spec")
    dr.add_argument("source")
    dr.add_argument("--limit", type=int, default=None)
    dr.set_defaults(fn=_cmd_dryrun)

    run = sub.add_parser("run")
    run.add_argument("spec")
    run.add_argument("source")
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--db", default="data/better_context.sqlite")
    run.set_defaults(fn=_cmd_run)

    on = sub.add_parser("onboard")
    on.add_argument("source")
    on.add_argument("--tenant", required=True)
    on.add_argument("--out", default=None)
    on.add_argument("--db", default="data/better_context.sqlite")
    on.add_argument("--sample-size", type=int, default=20)
    on.set_defaults(fn=_cmd_onboard)

    pr = sub.add_parser("promote")
    pr.add_argument("--tenant", required=True)
    pr.add_argument("--source-pattern", required=True)
    pr.add_argument("--version", type=int, required=True)
    pr.add_argument("--db", default="data/better_context.sqlite")
    pr.set_defaults(fn=_cmd_promote)

    ri = sub.add_parser("resolve-identity")
    ri.add_argument("--db", default="data/better_context.sqlite")
    ri.set_defaults(fn=_cmd_resolve_identity)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
