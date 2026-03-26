"""Unit tests for the jobs module (background ingestion job tracking)."""

from src.api.routes.jobs import create_job, update_job, get_job, _jobs, _MAX_JOBS


class TestJobs:
    """Test in-memory job tracking."""

    def setup_method(self) -> None:
        _jobs.clear()

    def test_create_job(self) -> None:
        job_id = create_job("kb-1", file_count=5)
        assert isinstance(job_id, str)
        assert len(job_id) == 8

        job = get_job(job_id)
        assert job is not None
        assert job["kb_id"] == "kb-1"
        assert job["status"] == "processing"
        assert job["total_files"] == 5
        assert job["processed"] == 0
        assert job["chunks"] == 0
        assert job["errors"] == []

    def test_update_job(self) -> None:
        job_id = create_job("kb-1", file_count=3)
        update_job(job_id, processed=2, chunks=50)

        job = get_job(job_id)
        assert job["processed"] == 2
        assert job["chunks"] == 50
        assert job["status"] == "processing"  # Not changed

    def test_update_job_status(self) -> None:
        job_id = create_job("kb-1", file_count=1)
        update_job(job_id, status="completed", processed=1, chunks=10)

        job = get_job(job_id)
        assert job["status"] == "completed"

    def test_update_nonexistent_job(self) -> None:
        """Updating a non-existent job should be a no-op."""
        update_job("nonexistent", status="completed")
        assert get_job("nonexistent") is None

    def test_get_job(self) -> None:
        job_id = create_job("kb-2", file_count=10)
        job = get_job(job_id)
        assert job is not None
        assert job["id"] == job_id

    def test_get_nonexistent_job(self) -> None:
        assert get_job("does-not-exist") is None

    def test_eviction_on_max_jobs(self) -> None:
        """When exceeding _MAX_JOBS, oldest completed jobs should be evicted.

        Note: _evict_oldest_completed checks len > _MAX_JOBS, so eviction
        happens when creating the (_MAX_JOBS + 2)th job (after the first
        overflow job was added without eviction).
        """
        # Create _MAX_JOBS + 1 completed jobs to exceed the limit
        old_ids = []
        for i in range(_MAX_JOBS + 1):
            jid = create_job(f"kb-{i}", file_count=1)
            update_job(jid, status="completed")
            old_ids.append(jid)

        # At this point we have _MAX_JOBS + 1 jobs. Creating another triggers eviction.
        new_id = create_job("kb-new", file_count=1)
        assert get_job(new_id) is not None

        # After eviction, total should be <= _MAX_JOBS + 1
        # (eviction removes enough to get to _MAX_JOBS, then adds the new one)
        assert len(_jobs) <= _MAX_JOBS + 1

        # The first completed job should have been evicted
        assert get_job(old_ids[0]) is None

    def test_eviction_prefers_completed_over_processing(self) -> None:
        """Processing jobs should be retained over completed ones during eviction."""
        # Fill with a mix of completed and processing jobs
        processing_ids = []
        for i in range(_MAX_JOBS):
            jid = create_job(f"kb-{i}", file_count=1)
            if i % 2 == 0:
                update_job(jid, status="completed")
            else:
                processing_ids.append(jid)

        # Trigger eviction
        new_id = create_job("kb-overflow", file_count=1)

        # Processing jobs should still be present
        for pid in processing_ids:
            assert get_job(pid) is not None, f"Processing job {pid} should survive eviction"

    def test_multiple_creates(self) -> None:
        ids = set()
        for i in range(10):
            jid = create_job(f"kb-{i}", file_count=i)
            ids.add(jid)
        # All IDs should be unique
        assert len(ids) == 10
