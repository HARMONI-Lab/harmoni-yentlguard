"""
YentlGuard Phoenix MCP client.

Queries Arize Phoenix via MCP for nb_ambiguous baseline ΔM values.
The correction gate in YentlGuardRunner calls this client to retrieve
the historical ΔM for a given vignette under the nb_ambiguous condition,
which serves as the recovery target for CRR computation.

Uses the correct mcp>=1.0.0 transport pattern:
    sse_client(url) → two anyio streams
    ClientSession(read_stream, write_stream) → session
"""

import asyncio
import json
import logging

from mcp.client.sse import sse_client
from mcp.client.session import ClientSession

logger = logging.getLogger(__name__)

# Default timeout for MCP tool calls (seconds)
_MCP_TIMEOUT = 15.0


class PhoenixMCPClient:
    """
    Thin client wrapping Phoenix MCP span queries for YentlGuard baseline lookup.

    Parameters
    ----------
    mcp_endpoint:
        Phoenix MCP server SSE URL, e.g. "http://localhost:6006/mcp/sse".
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

    # ── Internal async helpers ─────────────────────────────────────────────────

    async def _call_tool(self, tool_name: str, arguments: dict) -> list[dict]:
        """
        Open an SSE transport, create a ClientSession, call a tool, return
        the parsed JSON content blocks as a list of dicts.

        Raises RuntimeError on timeout or transport failure.
        """
        async with sse_client(self.mcp_endpoint) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=arguments)

        # result.content is a list of TextContent / ImageContent objects.
        # We expect TextContent blocks whose .text is JSON-encoded span data.
        parsed = []
        for block in result.content:
            text = getattr(block, "text", None)
            if text:
                try:
                    parsed.append(json.loads(text))
                except json.JSONDecodeError:
                    # Non-JSON text block — include as raw string
                    parsed.append({"raw": text})
        return parsed

    def _run(self, coro):
        """Run a coroutine with the MCP timeout, from synchronous context."""
        try:
            return asyncio.run(asyncio.wait_for(coro, timeout=_MCP_TIMEOUT))
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"PhoenixMCPClient timed out after {_MCP_TIMEOUT}s. "
                "Check Phoenix MCP server health."
            )

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_baseline_delta_m(
        self,
        vignette_id: str,
        variant: str = "nb_ambiguous",
        model_version: str | None = None,
    ) -> float:
        """
        Retrieve the mean ΔM for a vignette × variant from Phoenix span history.

        Queries Phoenix MCP for spans where:
            attributes["yentlguard.vignette_id"] == vignette_id
            attributes["yentlguard.demographic_variant"] == variant

        Returns the mean ΔM across matching spans.

        Raises
        ------
        ValueError
            If no matching spans are found or none contain a delta_m attribute.
        RuntimeError
            If the MCP query fails or times out.
        """
        arguments: dict = {
            "project_name": self.project_name,
            "filters": {
                "attributes.yentlguard.vignette_id": vignette_id,
                "attributes.yentlguard.demographic_variant": variant,
            },
        }
        if model_version:
            arguments["filters"]["attributes.yentlguard.model_version"] = model_version

        try:
            spans = self._run(self._call_tool("get_spans", arguments))
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(
                f"PhoenixMCPClient baseline lookup failed for "
                f"{vignette_id}/{variant}: {e}"
            ) from e

        if not spans:
            raise ValueError(
                f"No Phoenix spans found for vignette_id={vignette_id}, "
                f"variant={variant}. Run the baseline command first."
            )

        delta_m_values = [
            s.get("attributes", {}).get("yentlguard.delta_m")
            for s in spans
            if isinstance(s, dict) and s.get("attributes", {}).get("yentlguard.delta_m") is not None
        ]

        if not delta_m_values:
            raise ValueError(
                f"Spans found for {vignette_id}/{variant} but none contain "
                f"yentlguard.delta_m attribute. Verify YentlGuard span annotation."
            )

        mean_delta_m = sum(delta_m_values) / len(delta_m_values)
        logger.debug(
            "Phoenix baseline: vignette=%s variant=%s delta_m=%.4f (n=%d spans)",
            vignette_id, variant, mean_delta_m, len(delta_m_values),
        )
        return mean_delta_m

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
        arguments: dict = {
            "project_name": self.project_name,
            "filters": {"attributes.yentlguard.vignette_id": vignette_id},
            "limit": limit,
        }
        if variant:
            arguments["filters"]["attributes.yentlguard.demographic_variant"] = variant
        if model_version:
            arguments["filters"]["attributes.yentlguard.model_version"] = model_version

        try:
            return self._run(self._call_tool("get_spans", arguments))
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(
                f"PhoenixMCPClient span history query failed for {vignette_id}: {e}"
            ) from e
