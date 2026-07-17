from __future__ import annotations

from typing import Any

from core.evidence import Evidence, ProvenanceChain, make_evidence
from .verifier_models import ClaimChunk, VerificationReport
from .verifier_patterns import (
    DISCLAIMER_ALL_SELF_DECLARED,
    DISCLAIMER_FULLY_UNVERIFIED,
    DISCLAIMER_NO_CHAIN,
    DISCLAIMER_SESSION_MEMORY,
    SELF_DECLARED_PREFIXES,
    _NON_CLAIM_SECTIONS,
)
from .verifier_utils import (
    _find_semantic_support,
    _find_structured_support,
    _is_derivative_subagent_evidence,
    _merge_citation_only_chunks,
    _output_contract_header_name,
    _tool_citation_for,
    extract_statistical_figures,
    is_statistical_claim,
    is_structural_chunk,
    match_citation,
    parse_citations,
    split_into_chunks,
)


def verify(*, answer: str, chain: ProvenanceChain, llm: Any = None, user_question: str | None = None, receipt_ledger: Any = None, trace_id: str | None = None) -> VerificationReport:
    chain_empty = len(chain) == 0
    if user_question and user_question.strip():
        user_ev = make_evidence(kind="user_explicit", source_id="user:current_turn", obtained_via="user_input", claim="Operator-provided text for the current turn", excerpt=user_question.strip())
        local_chain = ProvenanceChain()
        for ev in chain.evidences:
            local_chain.add(ev)
        local_chain.add(user_ev)
        chain = local_chain
    all_chunks_text = _merge_citation_only_chunks(split_into_chunks(answer))
    if not all_chunks_text:
        return VerificationReport(total_chunks=0, verified_chunks=0, unverified_chunks=0, cited_but_unmatched_chunks=0, self_declared_chunks=0, structural_chunks=0, chunks=(), annotated_answer=answer, fully_unverified=True, chain_was_empty=chain_empty, disclaimer=(DISCLAIMER_NO_CHAIN if chain_empty else DISCLAIMER_FULLY_UNVERIFIED))
    examined_chunks: list[ClaimChunk] = []
    verified = unverified = cited_unmatched = topic_supported = memory_only_unmatched = self_declared = structural = 0
    annotated_chunks: list[str] = []
    # Index into ``annotated_chunks`` for each examined (non-structural) chunk,
    # so the downgrade pass below can patch the exact rendered line instead of
    # fuzzy string-matching (which silently failed when a claim began with its
    # own citation).
    examined_annotated_idx: list[int] = []
    current_section: str | None = None
    for chunk_text in all_chunks_text:
        header = _output_contract_header_name(chunk_text)
        if header is not None:
            current_section = header
            structural += 1
            annotated_chunks.append(chunk_text)
            continue
        if is_structural_chunk(chunk_text):
            structural += 1
            annotated_chunks.append(chunk_text)
            continue
        if current_section in _NON_CLAIM_SECTIONS:
            structural += 1
            annotated_chunks.append(chunk_text)
            continue
        cits = parse_citations(chunk_text)
        matched_ids: list[str] = []
        verdict: str
        annotated = chunk_text
        if not cits:
            struct_ev = _find_structured_support(chunk_text, chain) if not chain_empty else None
            if struct_ev is not None:
                verdict = "verified"
                verified += 1
                matched_ids.append(struct_ev.id)
                annotated = chunk_text.rstrip() + " " + _tool_citation_for(struct_ev)
            else:
                verdict = "unverified"
                unverified += 1
                annotated = chunk_text.rstrip() + " [unverified]"
        else:
            stat_figures = extract_statistical_figures(chunk_text)
            stat_claim = is_statistical_claim(chunk_text)
            any_matched = any_self_declared = any_topic_only = False
            topic_only_replacements: list[tuple[str, str]] = []
            for c in cits:
                if c.prefix in SELF_DECLARED_PREFIXES:
                    any_self_declared = True
                    body_part = f":{c.body}" if c.body else ""
                    annotated = annotated.replace(c.raw, f"[declared:{c.prefix}{body_part}]")
                    continue
                ev = match_citation(c, chain)
                if ev is None:
                    continue
                strict_ok = True
                if stat_claim and c.prefix not in {"user", "memory", "general-knowledge"}:
                    excerpt = ev.excerpt or ""
                    if stat_figures:
                        from .verifier_utils import _excerpt_supports_figures
                        if not _excerpt_supports_figures(excerpt, stat_figures):
                            strict_ok = False
                    else:
                        if ev.kind == "web_search_hit":
                            strict_ok = False
                body_part = f":{c.body}" if c.body else ""
                if strict_ok:
                    matched_ids.append(ev.id)
                    any_matched = True
                    annotated = annotated.replace(c.raw, f"[verified:{c.prefix}{body_part}]")
                else:
                    any_topic_only = True
                    topic_only_replacements.append((c.raw, f"[topic-only:{c.prefix}{body_part}]"))
            if any_matched:
                verdict = "verified"
                verified += 1
                for raw, rewrite in topic_only_replacements:
                    annotated = annotated.replace(raw, rewrite)
            elif any_self_declared:
                verdict = "self_declared"
                self_declared += 1
            elif any_topic_only:
                verdict = "topic_supported_but_claim_unverified"
                topic_supported += 1
                for raw, rewrite in topic_only_replacements:
                    annotated = annotated.replace(raw, rewrite)
                annotated = annotated.rstrip() + " [claim-figure-unverified]"
            else:
                struct_ev = _find_structured_support(chunk_text, chain) if chain.evidences and not chain_empty else None
                if struct_ev is not None:
                    verdict = "verified"
                    verified += 1
                    matched_ids.append(struct_ev.id)
                    for c in cits:
                        if c.prefix not in SELF_DECLARED_PREFIXES:
                            body_part = f":{c.body}" if c.body else ""
                            annotated = annotated.replace(c.raw, f"[verified:{c.prefix}{body_part}]")
                elif llm is not None and chain.evidences and not chain_empty:
                    sem_ev = _find_semantic_support(chunk_text, chain, llm)
                    if sem_ev is not None:
                        verdict = "verified"
                        verified += 1
                        matched_ids.append(sem_ev.id)
                        for c in cits:
                            if c.prefix not in SELF_DECLARED_PREFIXES:
                                body_part = f":{c.body}" if c.body else ""
                                annotated = annotated.replace(c.raw, f"[verified:{c.prefix}{body_part}]")
                    else:
                        verdict = "cited_but_unmatched"
                        cited_unmatched += 1
                else:
                    verdict = "cited_but_unmatched"
                    cited_unmatched += 1
                    if cits and all(c.prefix == "memory" or c.prefix in SELF_DECLARED_PREFIXES for c in cits):
                        memory_only_unmatched += 1
        examined_chunks.append(ClaimChunk(text=chunk_text, citations=tuple(cits), matched_evidence_ids=tuple(matched_ids), verdict=verdict))
        annotated_chunks.append(annotated)
        examined_annotated_idx.append(len(annotated_chunks) - 1)
    subagent_asserted = receipt_missing = 0
    if examined_chunks:
        from core.receipt_consumer import matched_evidence_lacks_receipt
        ev_by_id: dict[str, Evidence] = {ev.id: ev for ev in chain.evidences}
        rebuilt_chunks: list[ClaimChunk] = []
        for ch, ann_idx in zip(examined_chunks, examined_annotated_idx):
            if ch.verdict != "verified" or not ch.matched_evidence_ids:
                rebuilt_chunks.append(ch)
                continue
            evs = [ev_by_id.get(eid) for eid in ch.matched_evidence_ids]
            evs = [e for e in evs if e is not None]
            if not evs:
                rebuilt_chunks.append(ch)
                continue
            if all(_is_derivative_subagent_evidence(e) for e in evs):
                verified -= 1
                subagent_asserted += 1
                rebuilt_chunks.append(ClaimChunk(text=ch.text, citations=ch.citations, matched_evidence_ids=ch.matched_evidence_ids, verdict="subagent_asserted"))
                line = annotated_chunks[ann_idx]
                if "[subagent-asserted]" not in line:
                    annotated_chunks[ann_idx] = line.rstrip() + " [subagent-asserted]"
                continue
            if matched_evidence_lacks_receipt(evs, ledger=receipt_ledger, trace_id=trace_id):
                verified -= 1
                receipt_missing += 1
                rebuilt_chunks.append(ClaimChunk(text=ch.text, citations=ch.citations, matched_evidence_ids=ch.matched_evidence_ids, verdict="receipt_missing"))
                line = annotated_chunks[ann_idx]
                if "[no-receipt]" not in line:
                    annotated_chunks[ann_idx] = line.replace("[verified:", "[unverified:").rstrip() + " [no-receipt]"
                continue
            rebuilt_chunks.append(ch)
        examined_chunks = rebuilt_chunks
    annotated_answer = "\n".join(annotated_chunks)
    headers_found = any(_output_contract_header_name((t or "").strip()) is not None for t in all_chunks_text)
    malformed_output = bool(all_chunks_text) and not headers_found
    fully_unverified = (verified == 0 and self_declared == 0)
    disclaimer: str | None = None
    if fully_unverified:
        if cited_unmatched > 0 and cited_unmatched == memory_only_unmatched:
            disclaimer = DISCLAIMER_SESSION_MEMORY
        elif chain_empty:
            disclaimer = DISCLAIMER_NO_CHAIN
        else:
            disclaimer = DISCLAIMER_FULLY_UNVERIFIED
    elif verified == 0 and self_declared > 0:
        disclaimer = DISCLAIMER_ALL_SELF_DECLARED
    if disclaimer is not None:
        annotated_answer = annotated_answer.rstrip() + "\n\n" + disclaimer
    return VerificationReport(total_chunks=len(examined_chunks), verified_chunks=verified, unverified_chunks=unverified, cited_but_unmatched_chunks=cited_unmatched, self_declared_chunks=self_declared, structural_chunks=structural, chunks=tuple(examined_chunks), annotated_answer=annotated_answer, fully_unverified=fully_unverified, chain_was_empty=chain_empty, disclaimer=disclaimer, malformed_output=malformed_output, topic_supported_but_claim_unverified_chunks=topic_supported, subagent_asserted_chunks=subagent_asserted, receipt_missing_chunks=receipt_missing)
