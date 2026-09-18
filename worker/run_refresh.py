#!/usr/bin/env python3
"""Worker entrypoint for every refresh job kind.

Usage:
    python3 run_refresh.py --kind=weather   [--run=<id>]
    python3 run_refresh.py --kind=historical
    python3 run_refresh.py --kind=maintenance
    python3 run_refresh.py --kind=aoi --run=<id>

Scheduled CronJobs pass only --kind; the two UI buttons create a
refresh_runs row first and pass its id, so the API can report progress.
"""

from __future__ import annotations

import argparse
import sys
import traceback

import db


def _progress(conn, run_id):
    def cb(progress: float, phase: str, message: str) -> None:
        try:
            db.heartbeat(conn, run_id, progress, phase, message)
        except Exception:  # progress must never kill the job
            pass
    return cb


def run_weather(conn, run_id, progress) -> dict:
    import pipeline_score
    import sources_weather

    progress(0.1, "weather", "fetching Open-Meteo snapshot")
    snap = sources_weather.national_snapshot()
    if not snap:
        raise RuntimeError("weather provider returned no data")
    sources_weather.store_snapshot(conn, "nl", snap)
    progress(0.7, "scoring", "recomputing static scores")
    pipeline_score.recompute(conn)
    return {
        "as_of": snap["as_of"].isoformat(),
        "precip_14d": snap["precip_14d"],
        "dry_days": snap["dry_days"],
    }


def run_historical(conn, run_id, progress) -> dict:
    import pipeline_historical

    return pipeline_historical.run(conn, run_id, progress)


def run_maintenance(conn, run_id, progress) -> dict:
    import datetime as dt

    import pipeline_score

    progress(0.1, "recover", "reaping stale refresh runs")
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE refresh_runs
            SET status = 'failed', finished_at = now(),
                error = 'stale: no heartbeat', phase = 'stale'
            WHERE status IN ('queued', 'running')
              AND heartbeat_at < now() - interval '45 minutes'
            """
        )
        reaped = cur.rowcount
    conn.commit()

    progress(0.4, "scores", "recomputing static scores")
    scored = pipeline_score.recompute(conn)

    progress(0.8, "prune", "pruning old refresh history")
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM refresh_runs WHERE finished_at < now() - interval '60 days'"
        )
        pruned = cur.rowcount
    conn.commit()

    return {
        "reaped": reaped,
        "cell_scores": scored,
        "pruned_runs": pruned,
        "ran_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def run_aoi(conn, run_id, progress) -> dict:
    import pipeline_aoi

    return pipeline_aoi.run(conn, run_id, progress)


HANDLERS = {
    "weather": run_weather,
    "historical": run_historical,
    "maintenance": run_maintenance,
    "aoi": run_aoi,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True, choices=sorted(HANDLERS))
    parser.add_argument("--run", default=None)
    parser.add_argument("--requested-by", default=None)
    args = parser.parse_args(argv)

    conn = db.connect_with_retry()
    try:
        db.apply_migrations(conn)
    except Exception:
        # Migrations may already be applied by the API; only fail if the
        # schema is genuinely unusable.
        pass

    run_id = args.run or db.new_run_id()
    if not args.run:
        # Scheduled run: create the row so progress and history are recorded.
        db.start_run(conn, run_id, args.kind, None)

    db.start_run(conn, run_id, args.kind, args.requested_by)
    progress = _progress(conn, run_id)

    try:
        stats = HANDLERS[args.kind](conn, run_id, progress)
        db.finish_run(conn, run_id, "succeeded", stats)
        print("{} run {} succeeded: {}".format(args.kind, run_id, stats))
        return 0
    except Exception as exc:  # noqa: BLE001 - report any failure to the UI
        traceback.print_exc()
        db.finish_run(conn, run_id, "failed", None, str(exc)[:2000])
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
