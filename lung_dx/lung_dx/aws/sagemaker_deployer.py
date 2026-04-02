"""SageMaker 모델 배포 헬퍼.

CheXNet(DenseNet121) 모델을 SageMaker 엔드포인트로 배포한다.
settings.xray_backend="sagemaker"로 전환하면
phase1_xray/sagemaker_client.py가 이 엔드포인트를 호출한다.

배포 단계:
  1. 로컬 모델 가중치(.pth)를 model.tar.gz로 패키징
  2. S3에 업로드
  3. SageMaker Model 생성
  4. Endpoint Configuration 생성
  5. Endpoint 배포
"""

from __future__ import annotations

import logging
import tarfile
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class SageMakerDeployer:
    """CheXNet 모델 SageMaker 배포."""

    def __init__(
        self,
        bucket: str,
        region: str = "ap-northeast-2",
        role_arn: str = "",
    ):
        self.bucket = bucket
        self.region = region
        self.role_arn = role_arn
        self._sm_client = None
        self._s3_client = None

    def _get_sm_client(self):
        if self._sm_client is None:
            import boto3
            self._sm_client = boto3.client("sagemaker", region_name=self.region)
        return self._sm_client

    def _get_s3_client(self):
        if self._s3_client is None:
            import boto3
            self._s3_client = boto3.client("s3", region_name=self.region)
        return self._s3_client

    def package_model(self, weights_path: str | Path) -> Path:
        """모델 가중치를 model.tar.gz로 패키징.

        SageMaker가 요구하는 형식: model.tar.gz 내에
        model.pth + inference.py 포함.
        """
        weights_path = Path(weights_path)
        output = Path(tempfile.mkdtemp()) / "model.tar.gz"

        with tarfile.open(output, "w:gz") as tar:
            tar.add(weights_path, arcname="model.pth")

        logger.info("모델 패키징 완료: %s (%.1f MB)",
                     output, output.stat().st_size / 1024 / 1024)
        return output

    def upload_model(self, tar_path: str | Path) -> str:
        """model.tar.gz를 S3에 업로드.

        Returns:
            S3 URI.
        """
        tar_path = Path(tar_path)
        s3_key = f"models/chexnet/{tar_path.name}"

        client = self._get_s3_client()
        client.upload_file(str(tar_path), self.bucket, s3_key)

        uri = f"s3://{self.bucket}/{s3_key}"
        logger.info("S3 업로드 완료: %s", uri)
        return uri

    def deploy_endpoint(
        self,
        model_s3_uri: str,
        endpoint_name: str = "chexnet-endpoint",
        instance_type: str = "ml.g4dn.xlarge",
        framework_version: str = "2.1",
        py_version: str = "py310",
    ) -> str:
        """SageMaker 엔드포인트 배포.

        Args:
            model_s3_uri: S3에 업로드된 model.tar.gz URI
            endpoint_name: 엔드포인트 이름
            instance_type: 인스턴스 타입 (GPU 권장)
            framework_version: PyTorch 프레임워크 버전
            py_version: Python 버전

        Returns:
            생성된 엔드포인트 이름.
        """
        sm = self._get_sm_client()
        model_name = f"{endpoint_name}-model"
        config_name = f"{endpoint_name}-config"

        # PyTorch 컨테이너 이미지 URI
        image_uri = self._get_pytorch_image_uri(
            framework_version, py_version, instance_type
        )

        # 1) Model 생성
        sm.create_model(
            ModelName=model_name,
            PrimaryContainer={
                "Image": image_uri,
                "ModelDataUrl": model_s3_uri,
                "Environment": {
                    "SAGEMAKER_PROGRAM": "inference.py",
                },
            },
            ExecutionRoleArn=self.role_arn,
        )
        logger.info("SageMaker Model 생성: %s", model_name)

        # 2) Endpoint Configuration
        sm.create_endpoint_config(
            EndpointConfigName=config_name,
            ProductionVariants=[{
                "VariantName": "primary",
                "ModelName": model_name,
                "InstanceType": instance_type,
                "InitialInstanceCount": 1,
            }],
        )

        # 3) Endpoint 배포
        sm.create_endpoint(
            EndpointName=endpoint_name,
            EndpointConfigName=config_name,
        )
        logger.info("SageMaker Endpoint 배포 시작: %s (완료까지 5-10분)", endpoint_name)

        return endpoint_name

    def _get_pytorch_image_uri(
        self, fw_version: str, py_version: str, instance_type: str
    ) -> str:
        """리전별 SageMaker PyTorch 추론 컨테이너 URI."""
        account_map = {
            "ap-northeast-2": "763104351884",
            "us-east-1": "763104351884",
            "us-west-2": "763104351884",
        }
        account = account_map.get(self.region, "763104351884")
        processor = "gpu" if "g4" in instance_type or "p3" in instance_type else "cpu"
        return (
            f"{account}.dkr.ecr.{self.region}.amazonaws.com/"
            f"pytorch-inference:{fw_version}-{processor}-{py_version}"
        )

    def check_endpoint_status(self, endpoint_name: str) -> str:
        """엔드포인트 상태 확인."""
        sm = self._get_sm_client()
        response = sm.describe_endpoint(EndpointName=endpoint_name)
        status = response["EndpointStatus"]
        logger.info("Endpoint %s 상태: %s", endpoint_name, status)
        return status

    def delete_endpoint(self, endpoint_name: str) -> None:
        """엔드포인트 삭제 (비용 절감)."""
        sm = self._get_sm_client()
        sm.delete_endpoint(EndpointName=endpoint_name)
        sm.delete_endpoint_config(EndpointConfigName=f"{endpoint_name}-config")
        sm.delete_model(ModelName=f"{endpoint_name}-model")
        logger.info("Endpoint 삭제 완료: %s", endpoint_name)
