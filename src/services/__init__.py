"""Application services — cross-cutting helpers used by API + worker + CLI.

기존 ``src/api/`` 는 API 프로세스 전용 코드라 worker/CLI 가 import 하면
layering violation. 본 패키지는 process-agnostic helper (Redis, S3 URL
discovery 등) 만 담당.
"""
