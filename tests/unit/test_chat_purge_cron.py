from src.jobs.worker import WorkerSettings


def test_chat_purge_cron_registered_daily():
    sources = [str(c) for c in WorkerSettings.cron_jobs]
    matched = [s for s in sources if "chat_history_purge_sweep" in s]
    assert matched, "chat_history_purge_sweep cron not registered"
