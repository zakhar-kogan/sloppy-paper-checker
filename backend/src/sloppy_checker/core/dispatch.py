from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import BackgroundTasks

from .config import AppSettings

Runner = Callable[[str, AppSettings], Awaitable[None]]


class AnalysisDispatcher(Protocol):
    async def dispatch(self, analysis_id: str, background: BackgroundTasks) -> str | None: ...


class InlineAnalysisDispatcher:
    def __init__(self, settings: AppSettings, runner: Runner):
        self.settings = settings
        self.runner = runner

    async def dispatch(self, analysis_id: str, background: BackgroundTasks) -> str | None:
        background.add_task(self.runner, analysis_id, self.settings)
        return None


class NebiusJobDispatcher:
    """Creates one CPU job whose only per-analysis input is the analysis ID."""

    SECRET_ENV_NAMES = (
        "SPC_DATABASE_URL",
        "SPC_S3_ACCESS_KEY_ID",
        "SPC_S3_SECRET_ACCESS_KEY",
    )

    def __init__(self, settings: AppSettings, service: object | None = None):
        self.settings = settings
        self._service = service

    async def dispatch(self, analysis_id: str, background: BackgroundTasks) -> str:
        del background
        if not re.fullmatch(r"[0-9a-f-]{36}", analysis_id):
            raise ValueError("Invalid analysis ID")
        service = self._service
        if service is None:
            from nebius.api.nebius.ai.v1 import JobServiceClient
            from nebius.sdk import SDK

            service = JobServiceClient(SDK())
        request = self.build_request(analysis_id)
        operation = await service.create(request)
        return operation.resource_id

    def build_request(self, analysis_id: str):
        from nebius.api.nebius.ai.v1 import CreateJobRequest, JobSpec
        from nebius.api.nebius.common.v1 import ResourceMetadata

        secret = JobSpec.MysteryBoxSecretRef(secret_id=self.settings.nebius_job_secret_id)
        environment = [
            JobSpec.EnvironmentVariable(name="SPC_ANALYSIS_ID", value=analysis_id),
            JobSpec.EnvironmentVariable(name="SPC_DOCUMENT_STORE", value="s3"),
            JobSpec.EnvironmentVariable(
                name="SPC_S3_ENDPOINT_URL", value=self.settings.s3_endpoint_url or ""
            ),
            JobSpec.EnvironmentVariable(name="SPC_S3_REGION", value=self.settings.s3_region),
            JobSpec.EnvironmentVariable(name="SPC_S3_BUCKET", value=self.settings.s3_bucket or ""),
            JobSpec.EnvironmentVariable(
                name="SPC_PROVIDER_PROFILE",
                value=self.settings.provider_profile,
            ),
            JobSpec.EnvironmentVariable(
                name="SPC_PROVIDER_BASE_URL",
                value=self.settings.provider_base_url,
            ),
            JobSpec.EnvironmentVariable(
                name="SPC_PROVIDER_WORKER_MODEL",
                value=self.settings.configured_provider_worker_model,
            ),
            JobSpec.EnvironmentVariable(
                name="SPC_PROVIDER_REVIEWER_MODEL",
                value=self.settings.configured_provider_reviewer_model,
            ),
        ]
        environment.extend(
            JobSpec.EnvironmentVariable(name=name, mysterybox_secret=secret)
            for name in (*self.SECRET_ENV_NAMES, self.settings.provider_credential_env_name)
        )
        return CreateJobRequest(
            metadata=ResourceMetadata(
                parent_id=self.settings.nebius_project_id,
                name=f"spc-{analysis_id[:8]}",
            ),
            spec=JobSpec(
                image=self.settings.nebius_job_image,
                platform=self.settings.nebius_job_platform,
                preset=self.settings.nebius_job_preset,
                subnet_id=self.settings.nebius_job_subnet_id or "",
                public_ip=True,
                container_command="python",
                args="-m sloppy_checker.job",
                environment_variables=environment,
                restart_attempts=1,
            ),
        )


def get_analysis_dispatcher(
    settings: AppSettings,
    runner: Runner,
    *,
    nebius_service: object | None = None,
) -> AnalysisDispatcher:
    if settings.analysis_dispatcher == "inline":
        return InlineAnalysisDispatcher(settings, runner)
    if settings.analysis_dispatcher == "nebius_job":
        return NebiusJobDispatcher(settings, nebius_service)
    raise ValueError(f"Unsupported analysis dispatcher: {settings.analysis_dispatcher}")
