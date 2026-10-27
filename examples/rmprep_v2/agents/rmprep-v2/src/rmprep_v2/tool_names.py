"""The names of the agent's own evidence tools, shared by the tools, the guards and the checks."""

RETRIEVAL_TOOL_NAMES = ("search_evidence", "get_evidence", "get_memo_section", "get_analysis_note")
"""Tools that read the stored evidence of the memo the application selected."""

EVIDENCE_TOOL_NAMES = ("get_evidence", "get_memo_section")
"""The tools whose results make a reference citable: they return the records themselves.

A search hit is a candidate and an analysis note is an interpretation, so neither is here.
"""
