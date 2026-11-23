"""Unit tests for the OverviewService computing live aggregate metrics."""

import pytest
from backend.app.services.overview_service import OverviewService


@pytest.mark.asyncio
async def test_overview_metrics_empty_or_existing_db():
    service = OverviewService()
    metrics = await service.get_overview_metrics()

    assert "total_builds" in metrics
    assert "bit_for_bit_rate" in metrics
    assert "normalized_rate" in metrics
    assert "unresolved_count" in metrics
    assert "verdict_breakdown" in metrics
    assert "telemetry" in metrics
    assert "recent_builds" in metrics

    # Rates should have percentage symbol
    assert metrics["bit_for_bit_rate"].endswith("%")
    assert metrics["normalized_rate"].endswith("%")

    # Telemetry should be populated
    assert metrics["telemetry"]["cpu_count"] > 0
    assert metrics["telemetry"]["ram_gb"] > 0
    assert "os_name" in metrics["telemetry"]
