#!/usr/bin/env python3
"""Fail closed when a review artifact exposes searchable project history."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable


ALLOWED_GITHUB_URLS = {
    "https://github.com/Kaetram/Kaetram-Open",
}
ALLOWED_PDF_URLS = {
    *ALLOWED_GITHUB_URLS,
    "https://huggingface.co/collections/Qwen/qwen35",
    "https://arxiv.org/abs/2506.03610",
    "https://doi.org/10.18653/v1/2026.acl-long.1880",
}

_BIBTEX_ENTRY_TYPES = {
    "article",
    "book",
    "booklet",
    "conference",
    "inbook",
    "incollection",
    "inproceedings",
    "manual",
    "mastersthesis",
    "misc",
    "phdthesis",
    "proceedings",
    "techreport",
    "unpublished",
}

_GENERIC_LEAK_PATTERNS = {
    "numbered pull-request reference": re.compile(
        r"(?:\bPR\s*(?:[#~]\s*)?\d+\b|\bpull[\s~-]+request\s*(?:[#~]\s*)?\d+\b)",
        re.IGNORECASE,
    ),
    "shell-script filename": re.compile(r"\b[A-Za-z0-9_.-]+\.sh\b"),
    "service port in the 9000 range": re.compile(r"\b9\d{3}\b"),
    "local POSIX home path": re.compile(
        r"(?:/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+)(?:/[^\s{}]*)?"
    ),
    "local Windows path": re.compile(r"\b[A-Za-z]:\\[^\s{}]+"),
    "email address": re.compile(
        r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
        r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+\b"
    ),
    "source-control fingerprint": re.compile(
        r"\b(?:commit|branch)\s+(?:[0-9a-f]{7,40}|[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+)\b",
        re.IGNORECASE,
    ),
}

_MONTH_DAY = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2}\b",
    re.IGNORECASE,
)
_REPOSITORY_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:"
    r"(?:(?:https?|git|ssh)://)?(?:www\.)?"
    r"(?:github\.com|gitlab\.com|bitbucket\.org|codeberg\.org)"
    r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^\s}\])>,\\]*)?"
    r"|(?:ssh://)?git@(?:github\.com|gitlab\.com|bitbucket\.org|codeberg\.org)"
    r"(?::|/)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^\s}\])>,\\]*)?"
    r"|(?:https?://)?(?:raw\.githubusercontent\.com|codeload\.github\.com)"
    r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^\s}\])>,\\]*)?"
    r")",
    re.IGNORECASE,
)
_SOCIAL_HANDLE = re.compile(r"(?<![A-Za-z0-9_\\])@([A-Za-z0-9_][A-Za-z0-9_.-]{1,})")
_PDF_URL = re.compile(r"(?:https?|ftp)://\S+|mailto:\S+", re.IGNORECASE)


def _line_number(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def audit_text(label: str, text: str, *, check_history_dates: bool = False) -> list[str]:
    """Return human-readable findings for one source or extracted-text artifact."""

    findings: list[str] = []
    for description, pattern in _GENERIC_LEAK_PATTERNS.items():
        for match in pattern.finditer(text):
            findings.append(
                f"{label}:{_line_number(text, match.start())}: {description}"
            )

    for match in _REPOSITORY_REFERENCE.finditer(text):
        normalized = match.group(0).rstrip("/.,;:")
        allowed = {url.rstrip("/") for url in ALLOWED_GITHUB_URLS}
        if normalized.lower() not in {url.lower() for url in allowed}:
            findings.append(
                f"{label}:{_line_number(text, match.start())}: "
                "non-allowlisted repository reference"
            )

    for match in _SOCIAL_HANDLE.finditer(text):
        if match.group(1).lower() not in _BIBTEX_ENTRY_TYPES:
            findings.append(
                f"{label}:{_line_number(text, match.start())}: social-media handle"
            )

    if check_history_dates:
        for match in _MONTH_DAY.finditer(text):
            findings.append(
                f"{label}:{_line_number(text, match.start())}: searchable month-day history"
            )

    return findings


def audit_pdf_metadata(label: str, pdfinfo_text: str) -> list[str]:
    """Reject identifying metadata and unexpected creator/producer values."""

    findings: list[str] = []
    metadata: dict[str, str] = {}
    for line in pdfinfo_text.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            metadata[key.strip().lower()] = value.strip()

    for field in ("title", "subject", "keywords", "author"):
        if field not in metadata:
            findings.append(f"{label}: missing PDF metadata field: {field}")
        elif metadata[field]:
            findings.append(f"{label}: identifying PDF metadata field: {field}")
    if metadata.get("creator") != "LaTeX with hyperref":
        findings.append(f"{label}: unexpected PDF creator metadata")
    if not metadata.get("producer", "").startswith("pdfTeX-"):
        findings.append(f"{label}: unexpected PDF producer metadata")
    return findings


def audit_pdf_urls(label: str, pdf_url_text: str) -> list[str]:
    """Allow only public citation/infrastructure links in PDF annotations."""

    findings: list[str] = []
    nonempty_lines = [line.strip() for line in pdf_url_text.splitlines() if line.strip()]
    if not nonempty_lines or nonempty_lines[0].split() != ["Page", "Type", "URL"]:
        return [f"{label}: malformed or missing pdfinfo -url header"]

    allowed_exact = {url.lower() for url in ALLOWED_PDF_URLS}
    for line_number, line in enumerate(pdf_url_text.splitlines(), start=1):
        match = _PDF_URL.search(line)
        if not match:
            if "Annotation" in line:
                findings.append(f"{label}:{line_number}: malformed PDF annotation URL")
            continue
        url = match.group(0).rstrip(".,;:")
        if url.lower() not in allowed_exact:
            findings.append(f"{label}:{line_number}: non-allowlisted PDF annotation URL")
    return findings


def audit_submission(
    *,
    source_paths: Iterable[Path],
    bibliography_paths: Iterable[Path],
    pdf_text_paths: Iterable[Path] = (),
    pdf_info_paths: Iterable[Path] = (),
    pdf_url_paths: Iterable[Path] = (),
) -> list[str]:
    """Audit all review-facing representations of a submission."""

    findings: list[str] = []
    for path in source_paths:
        findings.extend(
            audit_text(
                str(path),
                path.read_text(encoding="utf-8", errors="replace"),
                check_history_dates=True,
            )
        )
    for path in (*tuple(bibliography_paths), *tuple(pdf_text_paths)):
        findings.extend(
            audit_text(
                str(path),
                path.read_text(encoding="utf-8", errors="replace"),
            )
        )
    for path in pdf_info_paths:
        pdfinfo_text = path.read_text(encoding="utf-8", errors="replace")
        findings.extend(audit_text(str(path), pdfinfo_text))
        findings.extend(
            audit_pdf_metadata(
                str(path),
                pdfinfo_text,
            )
        )
    for path in pdf_url_paths:
        findings.extend(
            audit_pdf_urls(
                str(path),
                path.read_text(encoding="utf-8", errors="replace"),
            )
        )
    return findings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", type=Path, required=True)
    parser.add_argument("--bibliography", action="append", type=Path, required=True)
    parser.add_argument("--pdf-text", action="append", type=Path, required=True)
    parser.add_argument("--pdf-info", action="append", type=Path, required=True)
    parser.add_argument("--pdf-urls", action="append", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    findings = audit_submission(
        source_paths=args.source,
        bibliography_paths=args.bibliography,
        pdf_text_paths=args.pdf_text,
        pdf_info_paths=args.pdf_info,
        pdf_url_paths=args.pdf_urls,
    )
    if findings:
        print("submission anonymity audit failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("submission anonymity audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
