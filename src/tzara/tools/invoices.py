"""
Invoice handling tools for Tzara.

Responsible for:
- Scanning inbox
- Parsing invoice metadata
- Planning renames
- Organizing invoices (dry-run or apply)
"""

from dataclasses import dataclass
from pathlib import Path
import re
from datetime import datetime
from typing import List, Tuple

from tzara.tools import fs


@dataclass
class Invoice:
    """
    Represents an invoice file with metadata.
    """

    vendor: str
    amount: float
    currency: str
    year: int
    month: int
    source_path: Path

    @property
    def target_filename(self) -> str:
        """Return the standardized filename."""
        return f"{self.vendor}_{self.year}-{self.month:02d}_{self.amount:.2f}_{self.currency}.pdf"

    @property
    def target_dir(self) -> Path:
        """Return the folder path where this invoice should be stored."""
        return config.INVOICES_ROOT / str(self.year) / f"{self.year}-{self.month:02d}"


def parse_invoice(text: str, source_path: Path) -> Invoice:
    """
    Extract metadata from the invoice text.

    Args:
        text: Text content of the invoice.
        source_path: Path to the original invoice PDF.

    Returns:
        Invoice object with metadata.
    """
    vendor = "UNKNOWN"
    text_lower = text.lower()

    if "aws" in text_lower:
        vendor = "AWS"
    elif "stripe" in text_lower:
        vendor = "Stripe"

    amount_match = re.search(r"(\d+\.\d{2})", text)
    amount = float(amount_match.group(1)) if amount_match else 0.0

    currency = "USD"

    now = datetime.now()
    return Invoice(
        vendor=vendor,
        amount=amount,
        currency=currency,
        year=now.year,
        month=now.month,
        source_path=source_path,
    )


def plan_organization() -> List[Tuple[Invoice, Path]]:
    """
    Scan inbox and plan invoice organization.

    Returns:
        List of tuples (Invoice, target_path)
    """
    plans: List[Tuple[Invoice, Path]] = []

    for pdf_path in fs.list_pdfs(config.INBOX_DIR):
        text = pdf.extract_text(pdf_path)
        invoice = parse_invoice(text, pdf_path)
        target_path = invoice.target_dir / invoice.target_filename
        plans.append((invoice, target_path))

    return
