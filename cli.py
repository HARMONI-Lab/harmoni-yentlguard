"""
YentlGuard CLI

Commands:
    baseline    Run nb_ambiguous vignettes through a model to populate Phoenix
                baseline spans. Must be run before any corrective loop.

    run         Execute two-pass mechanistic runs across all demographic variants
                for a vignette set, triggering correction gate where applicable.

    analyze     Pull completed run data from BigQuery, compute H1–H4 summary
                statistics, and write a self-contained HTML report + CSV files.

    report      Alias for analyze (backward compatibility).

Usage:
    yentlguard baseline --model gemini-2.5-pro --budget medium

    yentlguard run \\
        --model gemini-2.5-pro --budget medium \\
        --variants female nb_label_only \\
        --label "gemini-2.5 baseline May 2026"

    yentlguard run \\
        --model gemini-3.1-pro --budget low medium high \\
        --variants female nb_label_only nb_explicit

    yentlguard analyze \\
        --run-ids <run_id_1> <run_id_2> \\
        --output results/ \\
        --register-eval
"""

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("yentlguard.cli")


def cmd_baseline(args: argparse.Namespace) -> None:
    """Populate Phoenix with nb_ambiguous baseline spans."""
    from yentlguard.telemetry.phoenix import setup_phoenix_tracing
    from yentlguard.agent.runner import YentlGuardRunner

    logger.info("Initializing Phoenix tracing...")
    setup_phoenix_tracing()

    runner = YentlGuardRunner(
        model_version=args.model,
        thinking_budget=args.budget,
        phoenix_mcp_client=None,  # baseline pass: no MCP lookup needed
    )

    # Load vignettes from YentlBench
    from yentlbench.data import load_vignettes
    vignettes = load_vignettes(variant="nb_ambiguous")
    logger.info("Loaded %d nb_ambiguous vignettes from YentlBench.", len(vignettes))

    for v in vignettes:
        run = runner.run(
            vignette_id=v.vignette_id,
            vignette_text=v.text,
            demographic_variant="nb_ambiguous",
        )
        status = "✓" if not run.errors else "✗"
        dm = run.pass1_delta_m.delta_m if run.pass1_delta_m and run.pass1_delta_m.delta_m else None
        logger.info(
            "%s %s | ESI=%s | ΔM=%.4f",
            status,
            v.vignette_id,
            run.pass1_esi or "?",
            dm or 0.0,
        )

    logger.info("Baseline run complete. Spans available in Phoenix project: yentlguard")


def cmd_run(args: argparse.Namespace) -> None:
    """Execute two-pass mechanistic runs for specified variants."""
    import uuid
    from yentlguard.telemetry.phoenix import setup_phoenix_tracing
    from yentlguard.agent.runner import YentlGuardRunner
    from yentlguard.mcp.phoenix_client import PhoenixMCPClient
    from yentlguard.eval.bq_writer import BQWriter

    setup_phoenix_tracing()

    mcp_client = PhoenixMCPClient(mcp_endpoint=args.phoenix_mcp_endpoint)

    run_id = args.run_id or str(uuid.uuid4())
    logger.info("Experiment run_id: %s", run_id)

    from yentlbench.data import load_vignettes
    all_vignettes = load_vignettes(variant=args.variants[0])  # count for registration

    with BQWriter(run_id=run_id, gate_threshold=args.threshold) as bq:

        bq.register_experiment(
            label=args.label or f"{args.model} {','.join(args.budget)} {','.join(args.variants)}",
            models=[args.model],
            thinking_budgets=args.budget,
            variants=args.variants,
            vignette_count=len(all_vignettes) * len(args.variants) * len(args.budget),
            notes=args.notes,
        )

        for budget in args.budget:
            runner = YentlGuardRunner(
                model_version=args.model,
                thinking_budget=budget,
                delta_m_threshold=args.threshold,
                phoenix_mcp_client=mcp_client,
            )

            for variant in args.variants:
                vignettes = load_vignettes(variant=variant)
                logger.info(
                    "Running %d vignettes | model=%s | budget=%s | variant=%s",
                    len(vignettes), args.model, budget, variant,
                )
                for v in vignettes:
                    run = runner.run(
                        vignette_id=v.vignette_id,
                        vignette_text=v.text,
                        demographic_variant=variant,
                    )
                    bq.write(
                        run=run,
                        esi_ground_truth=getattr(v, "esi_ground_truth", None),
                        clinical_category=getattr(v, "clinical_category", None),
                    )
                    if run.crr:
                        logger.info(
                            "  %s | CRR=%.3f | ESI %s→%s | intervention=%s",
                            v.vignette_id,
                            run.crr.crr,
                            run.pass1_esi,
                            run.pass2_esi,
                            run.intervention_triggered,
                        )

    logger.info("Run complete. Query results: SELECT * FROM `%s` WHERE run_id = '%s'", "runs", run_id)


def cmd_report(args: argparse.Namespace) -> None:
    """Alias for analyze — kept for backward compatibility."""
    cmd_analyze(args)


def cmd_analyze(args: argparse.Namespace) -> None:
    """
    Pull completed run data from BigQuery, compute all summary statistics,
    and write a self-contained HTML report + CSV files to the output directory.
    """
    from pathlib import Path
    from datetime import datetime, timezone

    from yentlguard.eval.analyze import Analyzer
    from yentlguard.eval.report import generate_html_report
    from yentlguard.eval.export import export_csvs

    run_ids: list[str] = args.run_ids
    output_path = Path(args.output)

    if not run_ids:
        logger.error("No --run-ids provided. Pass at least one run_id to analyze.")
        return

    logger.info("Pulling data for %d run_id(s) from BigQuery...", len(run_ids))

    analyzer = Analyzer()
    result = analyzer.run(run_ids=run_ids)

    if result.raw_pass1.empty:
        logger.warning(
            "No data found for run_ids=%s. "
            "Verify run_ids exist in BigQuery and the runs table is populated.",
            run_ids,
        )
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # ── HTML report ────────────────────────────────────────────────────────
    logger.info("Generating HTML report...")
    html_path = generate_html_report(
        result=result,
        output_path=output_path,
        run_ids=run_ids,
    )
    logger.info("HTML report: %s", html_path)

    # ── CSV export ─────────────────────────────────────────────────────────
    logger.info("Exporting CSVs...")
    csv_files = export_csvs(
        result=result,
        output_path=output_path,
        timestamp=timestamp,
    )
    logger.info("Wrote %d CSV files to %s", len(csv_files), output_path)

    # ── Agent Builder eval task (optional) ────────────────────────────────
    if args.register_eval:
        from yentlguard.eval.agent_builder import AgentBuilderEvalLayer
        logger.info("Registering Agent Builder eval task...")
        try:
            layer = AgentBuilderEvalLayer()
            models = result.overview["model_version"].unique().tolist()
            task = layer.register_eval_task(
                run_ids=run_ids,
                label=args.label or f"yentlguard-analyze-{timestamp}",
                model_versions=models,
                notes=args.notes,
            )
            logger.info(
                "Agent Builder eval task registered: %s | models=%s",
                task.task_id,
                task.model_versions,
            )
        except Exception as e:
            logger.warning("Agent Builder registration failed (non-fatal): %s", e)

    # ── Summary to terminal ────────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("  YentlGuard Analysis Complete")
    print("─" * 60)
    print(f"  Run IDs analyzed : {len(run_ids)}")
    if not result.overview.empty:
        print(f"  Vignettes        : {int(result.overview['n_vignettes'].sum())}")
        models_str = ", ".join(result.overview["model_version"].unique().tolist())
        print(f"  Models           : {models_str}")
    print(f"  Interventions    : {len(result.raw_pass2)}")
    if not result.h4_crr.empty and result.h4_crr["mean_crr"].notna().any():
        mean_crr = result.h4_crr["mean_crr"].mean()
        print(f"  Mean CRR         : {mean_crr:.4f}")
    print(f"\n  HTML report → {html_path}")
    print(f"  CSVs        → {output_path}")
    print("─" * 60 + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yentlguard",
        description="Mechanistic interpretability layer for YentlBench triage bias analysis.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── baseline ──────────────────────────────────────────────────────────────
    p_baseline = sub.add_parser("baseline", help="Populate Phoenix nb_ambiguous baseline spans.")
    p_baseline.add_argument("--model", default="gemini-2.5-pro")
    p_baseline.add_argument("--budget", default="medium", choices=["low", "medium", "high"])
    p_baseline.set_defaults(func=cmd_baseline)

    # ── run ───────────────────────────────────────────────────────────────────
    p_run = sub.add_parser("run", help="Execute two-pass mechanistic runs.")
    p_run.add_argument("--model", required=True, help="e.g. gemini-2.5-pro or gemini-3.1-pro")
    p_run.add_argument(
        "--budget", nargs="+", default=["medium"],
        choices=["low", "medium", "high"],
        help="Thinking budget tier(s). Multiple values run sequentially.",
    )
    p_run.add_argument(
        "--variants", nargs="+",
        default=["female", "nb_label_only"],
        choices=["male", "female", "nb_ambiguous", "nb_label_only", "nb_explicit"],
    )
    p_run.add_argument(
        "--threshold", type=float, default=1.0,
        help="ΔM threshold below which correction gate fires (default: 1.0 nat).",
    )
    p_run.add_argument(
        "--phoenix-mcp-endpoint", default="http://localhost:6006/mcp",
        help="Phoenix MCP server URL for baseline ΔM lookup.",
    )
    p_run.add_argument(
        "--run-id", default=None,
        help="Experiment batch UUID. Auto-generated if not provided.",
    )
    p_run.add_argument(
        "--label", default=None,
        help="Human-readable experiment label for BigQuery experiments table.",
    )
    p_run.add_argument(
        "--notes", default=None,
        help="Free-text notes about this experiment batch.",
    )
    p_run.set_defaults(func=cmd_run)

    # ── analyze ───────────────────────────────────────────────────────────────
    p_analyze = sub.add_parser(
        "analyze",
        help="Pull BigQuery run data, compute summaries, write HTML report + CSVs.",
    )
    p_analyze.add_argument(
        "--run-ids", nargs="+", required=True,
        help="One or more experiment batch run_ids to include in this analysis.",
    )
    p_analyze.add_argument(
        "--output", default="results/",
        help="Output directory for HTML report and CSVs (default: results/).",
    )
    p_analyze.add_argument(
        "--register-eval", action="store_true", default=False,
        help="Register results as an Agent Builder eval task in Vertex AI.",
    )
    p_analyze.add_argument(
        "--label", default=None,
        help="Label for the Agent Builder eval task (used with --register-eval).",
    )
    p_analyze.add_argument(
        "--notes", default=None,
        help="Free-text notes attached to the Agent Builder eval task.",
    )
    p_analyze.set_defaults(func=cmd_analyze)

    # ── report (alias) ────────────────────────────────────────────────────────
    p_report = sub.add_parser(
        "report",
        help="Alias for analyze. Kept for backward compatibility.",
    )
    p_report.add_argument("--run-ids", nargs="+", required=True)
    p_report.add_argument("--output", default="results/")
    p_report.add_argument("--register-eval", action="store_true", default=False)
    p_report.add_argument("--label", default=None)
    p_report.add_argument("--notes", default=None)
    p_report.set_defaults(func=cmd_report)

    return parser


def main() -> None:
    from yentlguard.config import validate
    parser = build_parser()
    args = parser.parse_args()
    # Validate GCP config early — fails with a clear message before any API call
    if args.command in ("run", "baseline", "analyze", "report"):
        try:
            validate()
        except RuntimeError as e:
            print(f"\n{e}\n")
            raise SystemExit(1)
    args.func(args)


if __name__ == "__main__":
    main()
