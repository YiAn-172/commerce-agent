from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from apps.api.services import ApplicationServices
from apps.api.settings import Settings


async def main_async() -> None:
    settings = Settings()
    services = ApplicationServices(settings)
    try:
        expired = await services.approvals.expire_due()
        async with services.approval_runner() as workflow:
            recovered = await workflow.recover()
        report = {
            "status": "passed",
            "checked_at": datetime.now(UTC).isoformat(),
            "expired_count": len(expired),
            "expired_approval_ids": [item.approval_id for item in expired],
            "recovered_count": len(recovered),
            "recovered": [item.model_dump(mode="json") for item in recovered],
        }
        output = Path("reports/api/p7_recovery_worker.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        await services.close()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
