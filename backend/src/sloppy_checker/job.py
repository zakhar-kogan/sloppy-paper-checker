from __future__ import annotations

import asyncio
import os
from uuid import UUID

from sloppy_checker.core.config import get_settings
from sloppy_checker.core.database import AnalysisRow, SessionLocal
from sloppy_checker.workflows.analysis import execute_analysis


async def run() -> None:
    analysis_id = str(UUID(os.environ["SPC_ANALYSIS_ID"]))
    settings = get_settings()
    settings.validate_adapters()
    with SessionLocal() as session:
        await execute_analysis(analysis_id, session, settings)
        session.expire_all()
        row = session.get(AnalysisRow, analysis_id)
        if row and row.state == "failed":
            raise RuntimeError(f"Analysis failed: {row.error or 'unknown error'}")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
