"""
PhoenixMCPClient — queries Arize Phoenix via MCP for nb_ambiguous baseline ΔM.

The correction gate in YentlGuardRunner calls this client to retrieve the
historical ΔM for a given vignette under the nb_ambiguous condition, which
serves as the recovery target for CRR computation.

Phoenix exposes an MCP server at a known endpoint. We query it for spans
matching the vignette_id + variant combination and extract the stored
delta_m attribute from the span metadata.
"""

import logging
import asyncio
from typing import Any
import mcp

logger = logging.getLogger(__name__)


class PhoenixMCPClient:
    """
    Thin client wrapping Phoenix MCP span queries for YentlGuard baseline lookup.

    Parameters
    ----------
    mcp_endpoint:
        Phoenix MCP server URL, e.g. "http://localhost:6006/mcp".
    project_name:
        Phoenix project to scope queries to (default: "yentlguard").
    """

    def __init__(
        self,
        mcp_endpoint: str,
        project_name: str = "yentlguard",
    ):
        self.mcp_endpoint = mcp_endpoint
        self.project_name = project_name

    def get_baseline_delta_m(
        self,
        vignette_id: str,
        variant: str = "nb_ambiguous",
        model_version: str | None = None,
    ) -> float:
        """
        Retrieve the stored ΔM for a vignette × variant from Phoenix span history.

        Queries Phoenix MCP for spans where:
            span.attributes["vignette_id"] == vignette_id
            span.attributes["demographic_variant"] == variant

        Returns the mean ΔM across matching spans (to handle multiple runs).

        Raises
        ------
        ValueError
            If no matching spans are found for this vignette × variant.
        RuntimeError
            If the MCP query fails.
        """
        async def _query() -> float:
            async def _inner():
                async with mcp.ClientSession(self.mcp_endpoint) as session:
                    filters: dict[str, Any] = {
                        "project_name": self.project_name,
                        "attributes": {
                            "vignette_id": vignette_id,
                            "demographic_variant": variant,
                        },
                        "metric": "delta_m",
                    }
                    if model_version:
                        filters["attributes"]["model_version"] = model_version

                    result = await session.call_tool("get_spans", arguments=filters)

                    spans = result.content
                    if not spans:
                        raise ValueError(
                            f"No Phoenix spans found for vignette_id={vignette_id}, "
                            f"variant={variant}. Run nb_ambiguous baseline first."
                        )

                    delta_m_values = [
                        span["attributes"].get("delta_m")
                        for span in spans
                        if span.get("attributes", {}).get("delta_m") is not None
                    ]

                    if not delta_m_values:
                        raise ValueError(
                            f"Spans found for {vignette_id}/{variant} but none contain "
                            f"delta_m attribute. Verify YentlGuard span annotation."
                        )

                    mean_delta_m = sum(delta_m_values) / len(delta_m_values)
                    logger.debug(
                        "Phoenix baseline: vignette=%s variant=%s delta_m=%.4f (n=%d spans)",
                        vignette_id,
                        variant,
                        mean_delta_m,
                        len(delta_m_values),
                    )
                    return mean_delta_m
            return await asyncio.wait_for(_inner(), timeout=15.0)

        try:
            return asyncio.run(_query())
        except Exception as e:
            raise RuntimeError(
                f"PhoenixMCPClient query failed for {vignette_id}/{variant}: {e}"
            ) from e

    def get_span_history(
        self,
        vignette_id: str,
        variant: str | None = None,
        model_version: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        Return raw span records for a vignette for broader analysis.
        Useful for TAR distribution comparison and PSS computation.
        """
        async def _query() -> list[dict]:
            async def _inner():
                async with mcp.ClientSession(self.mcp_endpoint) as session:
                    filters: dict[str, Any] = {
                        "project_name": self.project_name,
                        "attributes": {"vignette_id": vignette_id},
                        "limit": limit,
                    }
                    if variant:
                        filters["attributes"]["demographic_variant"] = variant
                    if model_version:
                        filters["attributes"]["model_version"] = model_version

                    result = await session.call_tool("get_spans", arguments=filters)
                    return result.content or []
            return await asyncio.wait_for(_inner(), timeout=15.0)

        try:
            return asyncio.run(_query())
        except Exception as e:
            raise RuntimeError(
                f"PhoenixMCPClient span history query failed for {vignette_id}: {e}"
            ) from e
