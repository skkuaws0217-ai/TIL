"""S3 데이터 매니저.

로컬 파일 경로와 S3 경로를 투명하게 전환한다.
settings.data_backend="s3"일 때 활성화.

주요 기능:
  - YAML/Excel 참조 데이터 S3 업로드/다운로드
  - X-ray 이미지 S3 업로드/다운로드
  - Lab PDF S3에서 로컬로 다운로드 후 파싱
  - 소견서 결과 S3 업로드
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class S3Manager:
    """S3 데이터 업로드/다운로드."""

    def __init__(self, bucket: str, region: str = "ap-northeast-2"):
        self.bucket = bucket
        self.region = region
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def upload(self, local_path: str | Path, s3_key: str) -> str:
        """로컬 파일 → S3 업로드.

        Returns:
            S3 URI (s3://bucket/key)
        """
        local_path = Path(local_path)
        if not local_path.exists():
            raise FileNotFoundError(f"로컬 파일 없음: {local_path}")

        client = self._get_client()
        client.upload_file(str(local_path), self.bucket, s3_key)
        uri = f"s3://{self.bucket}/{s3_key}"
        logger.info("S3 업로드: %s → %s", local_path.name, uri)
        return uri

    def download(self, s3_key: str, local_path: str | Path) -> Path:
        """S3 → 로컬 다운로드.

        Returns:
            다운로드된 로컬 경로.
        """
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        client = self._get_client()
        client.download_file(self.bucket, s3_key, str(local_path))
        logger.info("S3 다운로드: s3://%s/%s → %s", self.bucket, s3_key, local_path)
        return local_path

    def download_if_not_cached(
        self, s3_key: str, cache_dir: str | Path
    ) -> Path:
        """S3에서 다운로드하되, 로컬 캐시에 있으면 재사용."""
        cache_dir = Path(cache_dir)
        local_path = cache_dir / Path(s3_key).name

        if local_path.exists():
            logger.debug("캐시 히트: %s", local_path)
            return local_path

        return self.download(s3_key, local_path)

    def upload_report(self, report_text: str, case_id: str) -> str:
        """소견서 텍스트를 S3에 Markdown으로 업로드."""
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(report_text)
            temp_path = f.name

        s3_key = f"reports/{case_id}.md"
        uri = self.upload(temp_path, s3_key)
        Path(temp_path).unlink()
        return uri

    def list_keys(self, prefix: str = "") -> list[str]:
        """S3 버킷 내 키 목록 조회."""
        client = self._get_client()
        response = client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        return [obj["Key"] for obj in response.get("Contents", [])]
