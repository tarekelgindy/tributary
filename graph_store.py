"""
Tributary Graph Storage
========================
Stores provenance chains in Neo4j, integrating with the existing
Tributary graph model (Commentator, Content, Source, Ecosystem nodes).

New node types:
  - Claim: A discrete factual assertion extracted from Content
  - PrimarySource: The original source of a piece of information

New edge types:
  - CONTAINS_CLAIM: Content -> Claim
  - TRACED_TO: Claim -> Source (with tier and confidence)
  - ORIGINATES_FROM: Claim -> PrimarySource
  - DERIVED_FROM: Source -> Source (citation chain)
"""

from dataclasses import asdict
from typing import Optional

from neo4j import AsyncGraphDatabase, AsyncDriver

from agent import ProvenanceChain, Claim, Source, ContentLabel, SourceTier


class TributaryGraph:
    """
    Graph storage for Tributary provenance data.
    
    Integrates with existing schema:
      (:Commentator)-[:PRODUCES]->(:Content)
      (:Content)-[:CITES]->(:Source)
      (:Commentator)-[:BELONGS_TO]->(:Ecosystem)
    
    Adds provenance layer:
      (:Content)-[:CONTAINS_CLAIM]->(:Claim)
      (:Claim)-[:TRACED_TO {tier, confidence}]->(:Source)
      (:Claim)-[:ORIGINATES_FROM]->(:PrimarySource)
      (:Source)-[:DERIVED_FROM]->(:Source)
    """

    def __init__(self, uri: str, user: str, password: str):
        self.driver: AsyncDriver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async def close(self):
        await self.driver.close()

    # -- Schema setup --

    async def create_constraints(self):
        """Create uniqueness constraints and indexes."""
        async with self.driver.session() as session:
            constraints = [
                "CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (c:Claim) REQUIRE c.claim_id IS UNIQUE",
                "CREATE CONSTRAINT source_url IF NOT EXISTS FOR (s:Source) REQUIRE s.url IS UNIQUE",
                "CREATE INDEX claim_text IF NOT EXISTS FOR (c:Claim) ON (c.text_hash)",
                "CREATE INDEX claim_label IF NOT EXISTS FOR (c:Claim) ON (c.label)",
                "CREATE INDEX source_tier IF NOT EXISTS FOR (s:Source) ON (s.tier)",
            ]
            for stmt in constraints:
                try:
                    await session.run(stmt)
                except Exception as e:
                    print(f"Constraint warning (may already exist): {e}")

    # -- Store provenance chains --

    async def store_chain(
        self,
        chain: ProvenanceChain,
        content_id: Optional[str] = None,
    ) -> str:
        """
        Store a complete provenance chain in the graph.
        
        If content_id is provided, links the claim to an existing Content node.
        Returns the claim_id.
        """
        async with self.driver.session() as session:
            # 1. Create or merge the Claim node
            await session.run(
                """
                MERGE (c:Claim {claim_id: $claim_id})
                SET c.text = $text,
                    c.label = $label,
                    c.context = $context,
                    c.text_hash = $text_hash,
                    c.confidence = $confidence,
                    c.chain_complete = $chain_complete,
                    c.summary = $summary,
                    c.traced_at = $traced_at,
                    c.hops = $hops
                """,
                claim_id=chain.claim.claim_id,
                text=chain.claim.text,
                label=chain.claim.label.value,
                context=chain.claim.context,
                text_hash=chain.claim.claim_id,  # Already a hash
                confidence=chain.confidence,
                chain_complete=chain.chain_complete,
                summary=chain.summary,
                traced_at=chain.traced_at,
                hops=chain.hops,
            )

            # 2. Link to Content node if provided
            if content_id:
                await session.run(
                    """
                    MATCH (content:Content {id: $content_id})
                    MATCH (claim:Claim {claim_id: $claim_id})
                    MERGE (content)-[:CONTAINS_CLAIM {extracted_at: $traced_at}]->(claim)
                    """,
                    content_id=content_id,
                    claim_id=chain.claim.claim_id,
                    traced_at=chain.traced_at,
                )

            # 3. Create Source nodes and TRACED_TO edges
            for source in chain.sources:
                await session.run(
                    """
                    MERGE (s:Source {url: $url})
                    SET s.title = $title,
                        s.tier = $tier,
                        s.relevant_excerpt = $excerpt,
                        s.retrieved_at = $retrieved_at
                    
                    WITH s
                    MATCH (c:Claim {claim_id: $claim_id})
                    MERGE (c)-[r:TRACED_TO]->(s)
                    SET r.confidence = $confidence,
                        r.tier = $tier,
                        r.traced_at = $traced_at
                    """,
                    url=source.url,
                    title=source.title,
                    tier=source.tier.value,
                    excerpt=source.relevant_excerpt,
                    retrieved_at=source.retrieved_at,
                    claim_id=chain.claim.claim_id,
                    confidence=source.confidence,
                    traced_at=chain.traced_at,
                )

                # Create DERIVED_FROM edges (citation chain)
                if source.cites:
                    await session.run(
                        """
                        MERGE (child:Source {url: $child_url})
                        MERGE (parent:Source {url: $parent_url})
                        MERGE (child)-[:DERIVED_FROM {discovered_at: $traced_at}]->(parent)
                        """,
                        child_url=source.url,
                        parent_url=source.cites,
                        traced_at=chain.traced_at,
                    )

            # 4. Mark primary source with ORIGINATES_FROM edge
            if chain.primary_source:
                await session.run(
                    """
                    MATCH (c:Claim {claim_id: $claim_id})
                    MATCH (s:Source {url: $url})
                    MERGE (c)-[:ORIGINATES_FROM {confidence: $confidence}]->(s)
                    SET s:PrimarySource
                    """,
                    claim_id=chain.claim.claim_id,
                    url=chain.primary_source.url,
                    confidence=chain.confidence,
                )

            return chain.claim.claim_id

    # -- Query methods --

    async def get_chain(self, claim_id: str) -> Optional[dict]:
        """Retrieve a stored provenance chain by claim ID."""
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (c:Claim {claim_id: $claim_id})
                OPTIONAL MATCH (c)-[t:TRACED_TO]->(s:Source)
                OPTIONAL MATCH (c)-[:ORIGINATES_FROM]->(primary:Source)
                RETURN c, 
                       collect(DISTINCT {source: s, rel: t}) AS sources,
                       primary
                """,
                claim_id=claim_id,
            )
            record = await result.single()
            if not record:
                return None

            claim_node = record["c"]
            return {
                "claim": dict(claim_node),
                "sources": [
                    {**dict(s["source"]), "trace_confidence": s["rel"]["confidence"]}
                    for s in record["sources"] if s["source"] is not None
                ],
                "primary_source": dict(record["primary"]) if record["primary"] else None,
            }

    async def find_similar_claims(self, claim_text: str, limit: int = 5) -> list[dict]:
        """
        Find claims similar to the given text using full-text search.
        This enables the cache-hit optimization — if someone else already
        traced a similar claim, we can return that result.
        
        Requires a full-text index:
          CREATE FULLTEXT INDEX claim_fulltext IF NOT EXISTS 
          FOR (c:Claim) ON EACH [c.text]
        """
        async with self.driver.session() as session:
            result = await session.run(
                """
                CALL db.index.fulltext.queryNodes('claim_fulltext', $query)
                YIELD node, score
                WHERE score > 0.5
                RETURN node.claim_id AS claim_id, 
                       node.text AS text, 
                       node.confidence AS confidence,
                       node.summary AS summary,
                       score
                ORDER BY score DESC
                LIMIT $limit
                """,
                query=claim_text,
                limit=limit,
            )
            return [dict(record) async for record in result]

    async def get_provenance_for_content(self, content_id: str) -> list[dict]:
        """
        Get all traced claims for a piece of content.
        This powers the visualization: "here are all the factual claims
        in this article and where they come from."
        """
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (content:Content {id: $content_id})-[:CONTAINS_CLAIM]->(claim:Claim)
                OPTIONAL MATCH (claim)-[:ORIGINATES_FROM]->(primary:Source)
                OPTIONAL MATCH (claim)-[t:TRACED_TO]->(s:Source)
                RETURN claim.text AS claim_text,
                       claim.label AS label,
                       claim.confidence AS confidence,
                       claim.summary AS summary,
                       primary.url AS primary_source_url,
                       collect(DISTINCT {
                           url: s.url, 
                           tier: t.tier, 
                           confidence: t.confidence
                       }) AS source_chain
                ORDER BY claim.confidence DESC
                """,
                content_id=content_id,
            )
            return [dict(record) async for record in result]

    async def get_claim_spread(self, claim_id: str) -> list[dict]:
        """
        Find all Content nodes that contain this same claim.
        This answers: "who else is saying this, and where did THEY get it?"
        
        This is the core Tributary insight — seeing how a single claim
        propagates across content creators.
        """
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (claim:Claim {claim_id: $claim_id})
                MATCH (content:Content)-[:CONTAINS_CLAIM]->(claim)
                OPTIONAL MATCH (commentator:Commentator)-[:PRODUCES]->(content)
                OPTIONAL MATCH (commentator)-[:BELONGS_TO]->(eco:Ecosystem)
                RETURN content.id AS content_id,
                       content.title AS content_title,
                       content.published_at AS published_at,
                       commentator.name AS commentator,
                       eco.name AS ecosystem
                ORDER BY content.published_at ASC
                """,
                claim_id=claim_id,
            )
            return [dict(record) async for record in result]

    async def provenance_stats(self) -> dict:
        """Dashboard stats for the provenance system."""
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (c:Claim) 
                WITH count(c) AS total_claims
                
                OPTIONAL MATCH (c2:Claim)-[:ORIGINATES_FROM]->(:PrimarySource)
                WITH total_claims, count(c2) AS traced_to_primary
                
                OPTIONAL MATCH (c3:Claim {chain_complete: true})
                WITH total_claims, traced_to_primary, count(c3) AS complete_chains
                
                OPTIONAL MATCH (:Claim)-[:TRACED_TO]->(s:Source)
                WITH total_claims, traced_to_primary, complete_chains, 
                     count(DISTINCT s) AS unique_sources
                
                RETURN total_claims, traced_to_primary, complete_chains, unique_sources
                """
            )
            record = await result.single()
            return dict(record) if record else {}
