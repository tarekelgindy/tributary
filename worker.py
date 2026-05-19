"""
Tributary Trace Worker & API
==============================
Background worker for processing trace jobs, plus a lightweight API
for the browser extension to submit and retrieve traces.

In production, use a proper task queue (Celery, Temporal, BullMQ).
This uses asyncio for simplicity.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from agent import SourceTracer, WebSearchAdapter, TraceCache, Claim, ContentLabel


# ---------------------------------------------------------------------------
# Job queue
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class TraceJob:
    job_id: str
    content: Optional[str] = None         # Full content to extract claims from
    claim_text: Optional[str] = None      # Single claim to trace directly
    content_id: Optional[str] = None      # Link to existing Content node
    status: JobStatus = JobStatus.QUEUED
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: str = ""
    completed_at: Optional[str] = None
    cost_usd: float = 0.0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


class JobQueue:
    """
    In-memory job queue for development.
    Replace with Redis/Celery/Temporal in production.
    """

    def __init__(self):
        self._jobs: dict[str, TraceJob] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def submit(
        self,
        content: Optional[str] = None,
        claim_text: Optional[str] = None,
        content_id: Optional[str] = None,
    ) -> str:
        job_id = str(uuid.uuid4())[:8]
        job = TraceJob(
            job_id=job_id,
            content=content,
            claim_text=claim_text,
            content_id=content_id,
        )
        self._jobs[job_id] = job
        await self._queue.put(job_id)
        return job_id

    async def get_next(self) -> Optional[TraceJob]:
        try:
            job_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            return self._jobs.get(job_id)
        except asyncio.TimeoutError:
            return None

    def get_job(self, job_id: str) -> Optional[TraceJob]:
        return self._jobs.get(job_id)

    def update_job(self, job: TraceJob):
        self._jobs[job.job_id] = job


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class TraceWorker:
    """
    Background worker that processes trace jobs from the queue.
    
    Run as a long-lived process:
        worker = TraceWorker(queue, tracer)
        await worker.run()
    """

    def __init__(
        self,
        queue: JobQueue,
        tracer: SourceTracer,
        graph=None,           # Optional TributaryGraph for persistence
        max_concurrent: int = 3,
    ):
        self.queue = queue
        self.tracer = tracer
        self.graph = graph
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def process_job(self, job: TraceJob):
        """Process a single trace job."""
        async with self.semaphore:
            job.status = JobStatus.RUNNING
            self.queue.update_job(job)

            try:
                if job.content:
                    # Full content: extract and trace all claims
                    chains = await self.tracer.trace_content(job.content)
                    job.result = {
                        "chains": [c.to_dict() for c in chains],
                        "total_claims": len(chains),
                        "traced_to_primary": sum(
                            1 for c in chains if c.primary_source
                        ),
                    }

                    # Persist to graph if available
                    if self.graph:
                        for chain in chains:
                            await self.graph.store_chain(
                                chain, content_id=job.content_id
                            )

                elif job.claim_text:
                    # Single claim: trace directly
                    claim = Claim(
                        text=job.claim_text,
                        label=ContentLabel.FACT,
                        context="",
                    )
                    chain = await self.tracer.trace_claim(claim)
                    job.result = chain.to_dict()

                    if self.graph:
                        await self.graph.store_chain(
                            chain, content_id=job.content_id
                        )

                job.status = JobStatus.COMPLETE
                job.cost_usd = self.tracer.usage["cost_usd"]

            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = str(e)

            job.completed_at = datetime.now(timezone.utc).isoformat()
            self.queue.update_job(job)

    async def run(self):
        """Main worker loop. Runs forever, processing jobs as they arrive."""
        print("[worker] Starting trace worker...")
        while True:
            job = await self.queue.get_next()
            if job:
                print(f"[worker] Processing job {job.job_id}")
                # Fire and forget — semaphore handles concurrency
                asyncio.create_task(self.process_job(job))


# ---------------------------------------------------------------------------
# API endpoints (framework-agnostic request handlers)
# ---------------------------------------------------------------------------

class TraceAPI:
    """
    API handlers for the trace service.
    
    Wire these into your web framework (FastAPI, Flask, etc.):
    
        app = FastAPI()
        api = TraceAPI(queue)
        
        @app.post("/trace/content")
        async def trace_content(request):
            return await api.submit_content(request.json())
        
        @app.post("/trace/claim")
        async def trace_claim(request):
            return await api.submit_claim(request.json())
        
        @app.get("/trace/{job_id}")
        async def get_result(job_id: str):
            return await api.get_result(job_id)
    """

    def __init__(self, queue: JobQueue, graph=None):
        self.queue = queue
        self.graph = graph

    async def submit_content(self, body: dict) -> dict:
        """
        POST /trace/content
        Body: {"content": "article text...", "content_id": "optional-existing-id"}
        
        Submits full content for claim extraction and tracing.
        Returns a job ID for polling.
        """
        content = body.get("content")
        if not content:
            return {"error": "content is required"}, 400

        job_id = await self.queue.submit(
            content=content,
            content_id=body.get("content_id"),
        )
        return {"job_id": job_id, "status": "queued"}

    async def submit_claim(self, body: dict) -> dict:
        """
        POST /trace/claim
        Body: {"claim": "the unemployment rate fell to 4.1%"}
        
        Submits a single highlighted claim for tracing.
        This is the "highlight to trace" interaction from the extension.
        """
        claim_text = body.get("claim")
        if not claim_text:
            return {"error": "claim is required"}, 400

        # Check graph for existing traces of this claim
        if self.graph:
            similar = await self.graph.find_similar_claims(claim_text)
            if similar and similar[0].get("score", 0) > 0.9:
                # High-confidence match — return cached result
                cached = await self.graph.get_chain(similar[0]["claim_id"])
                if cached:
                    return {
                        "status": "cached",
                        "result": cached,
                        "similar_claims": similar,
                    }

        job_id = await self.queue.submit(
            claim_text=claim_text,
            content_id=body.get("content_id"),
        )
        return {"job_id": job_id, "status": "queued"}

    async def get_result(self, job_id: str) -> dict:
        """
        GET /trace/{job_id}
        
        Poll for job status and results.
        """
        job = self.queue.get_job(job_id)
        if not job:
            return {"error": "job not found"}, 404

        response = {
            "job_id": job.job_id,
            "status": job.status.value,
            "created_at": job.created_at,
        }

        if job.status == JobStatus.COMPLETE:
            response["result"] = job.result
            response["completed_at"] = job.completed_at
            response["cost_usd"] = job.cost_usd

        elif job.status == JobStatus.FAILED:
            response["error"] = job.error

        return response

    async def get_content_provenance(self, content_id: str) -> dict:
        """
        GET /provenance/{content_id}
        
        Get all traced claims for a piece of content.
        Powers the main Tributary visualization.
        """
        if not self.graph:
            return {"error": "graph store not configured"}, 500

        claims = await self.graph.get_provenance_for_content(content_id)
        return {
            "content_id": content_id,
            "claims": claims,
            "total": len(claims),
            "with_primary_source": sum(
                1 for c in claims if c.get("primary_source_url")
            ),
        }

    async def get_claim_spread(self, claim_id: str) -> dict:
        """
        GET /spread/{claim_id}
        
        See how a claim has spread across content creators.
        "Who else is saying this, and do they trace to the same source?"
        """
        if not self.graph:
            return {"error": "graph store not configured"}, 500

        spread = await self.graph.get_claim_spread(claim_id)
        return {
            "claim_id": claim_id,
            "appearances": spread,
            "total_appearances": len(spread),
            "ecosystems": list({
                s["ecosystem"] for s in spread if s.get("ecosystem")
            }),
        }


# ---------------------------------------------------------------------------
# Startup example
# ---------------------------------------------------------------------------

async def start_server():
    """Example of how to wire everything together."""

    # Initialize components
    search = WebSearchAdapter(api_key="your-search-api-key")
    cache = TraceCache()
    tracer = SourceTracer(search_adapter=search, cache=cache)
    queue = JobQueue()
    # graph = TributaryGraph("bolt://localhost:7687", "neo4j", "password")
    # await graph.create_constraints()

    api = TraceAPI(queue=queue, graph=None)
    worker = TraceWorker(queue=queue, tracer=tracer, graph=None)

    # Start background worker
    worker_task = asyncio.create_task(worker.run())

    # Example: submit a trace job
    result = await api.submit_claim({
        "claim": "The US economy added 256,000 jobs in December 2024"
    })
    print(f"Submitted: {result}")

    # Poll for result
    job_id = result["job_id"]
    while True:
        status = await api.get_result(job_id)
        print(f"Status: {status['status']}")
        if status["status"] in ("complete", "failed"):
            print(json.dumps(status, indent=2, default=str))
            break
        await asyncio.sleep(2)

    worker_task.cancel()
    await search.close()


if __name__ == "__main__":
    asyncio.run(start_server())
