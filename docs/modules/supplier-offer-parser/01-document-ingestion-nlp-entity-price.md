# Supplier Offer Parser — Sekcje 1–4

## 1. Document Ingestion

### 1.1 Obsługiwane formaty i kanały wejściowe

```python
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


class OfferFormat(str, Enum):
    EMAIL_HTML      = "EMAIL_HTML"       # HTML body z e-maila
    EMAIL_TEXT      = "EMAIL_TEXT"       # Plaintext e-mail
    PDF             = "PDF"              # Oferta PDF (vector lub skan)
    EXCEL           = "EXCEL"            # XLS / XLSX z tabelą cen
    CSV             = "CSV"              # Plik CSV dostawcy
    WORD            = "WORD"             # DOCX
    EDI_X12         = "EDI_X12"          # ANSI X12 (855 Purchase Order Ack)
    EDI_EDIFACT     = "EDI_EDIFACT"      # UN/EDIFACT QUOTES D96A
    PUNCHOUT_XML    = "PUNCHOUT_XML"     # cXML PunchOut catalog
    JSON_API        = "JSON_API"         # Supplier REST API response
    ERP_IDOC        = "ERP_IDOC"         # SAP IDoc QUOTES01
    MANUAL_FORM     = "MANUAL_FORM"      # Web form (structured JSON)


class IngestionChannel(str, Enum):
    EMAIL_IMAP      = "EMAIL_IMAP"
    EMAIL_API       = "EMAIL_API"        # SendGrid / MS Graph
    SFTP            = "SFTP"
    HTTP_WEBHOOK    = "HTTP_WEBHOOK"
    S3_DROP         = "S3_DROP"          # S3 bucket watch
    MANUAL_UPLOAD   = "MANUAL_UPLOAD"
    EDI_AS2         = "EDI_AS2"
    API_POLL        = "API_POLL"         # Supplier portal API


class OfferStatus(str, Enum):
    RECEIVED        = "RECEIVED"
    PARSING         = "PARSING"
    PARSED          = "PARSED"
    VALIDATED       = "VALIDATED"
    MAPPED          = "MAPPED"
    REJECTED        = "REJECTED"
    NEEDS_REVIEW    = "NEEDS_REVIEW"
    ARCHIVED        = "ARCHIVED"


@dataclass
class RawOffer:
    offer_id: str
    channel: IngestionChannel
    format: OfferFormat
    raw_content: bytes
    content_type: str                    # MIME type
    filename: Optional[str]
    sender_email: Optional[str]
    sender_domain: Optional[str]
    subject: Optional[str]
    received_at: str                     # ISO 8601
    checksum_sha256: str
    file_size_bytes: int
    metadata: dict = field(default_factory=dict)   # channel-specific headers


@dataclass
class OfferDocument:
    """Normalized representation after initial ingestion."""
    offer_id: str
    raw_offer_id: str
    format: OfferFormat
    channel: IngestionChannel
    text_content: str               # Extracted plaintext
    structured_data: Optional[dict] # For JSON/XML/EDI — pre-parsed structure
    pages: int
    language: str                   # ISO 639-1
    encoding: str
    supplier_hint: Optional[str]    # Domain or sender name
    rfq_ref: Optional[str]          # RFQ reference from content
    currency_hint: Optional[str]    # Detected currency
    extraction_quality: float       # 0.0–1.0
```

### 1.2 Email Ingestor

```python
import asyncio
import imaplib
import email
from email import policy
from email.header import decode_header
import hashlib
import structlog

log = structlog.get_logger()


class EmailIMAPIngestor:
    """
    Polls IMAP mailbox for new supplier offer emails.
    Handles multipart MIME: extracts body + all attachments.
    Supports OAuth2 (MS Graph) and app-password authentication.
    """
    FOLDERS = ["INBOX", "Offers", "Supplier Quotes", "Angebote"]
    POLL_INTERVAL_S = 60
    MAX_EMAIL_SIZE  = 50 * 1024 * 1024   # 50 MB

    def __init__(self, host: str, port: int, username: str,
                 password: str, ssl: bool = True):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.ssl = ssl

    async def run(self, callback):
        """Continuously poll — call callback(RawOffer) for each new email."""
        while True:
            try:
                await self._poll(callback)
            except Exception as e:
                log.error("imap_poll_error", error=str(e))
            await asyncio.sleep(self.POLL_INTERVAL_S)

    async def _poll(self, callback):
        conn = imaplib.IMAP4_SSL(self.host, self.port) if self.ssl \
               else imaplib.IMAP4(self.host, self.port)
        conn.login(self.username, self.password)
        try:
            for folder in self.FOLDERS:
                try:
                    conn.select(folder)
                except Exception:
                    continue
                _, msg_nums = conn.search(None, "UNSEEN")
                for num in (msg_nums[0] or b"").split():
                    _, data = conn.fetch(num, "(RFC822)")
                    if not data or not data[0]:
                        continue
                    raw = data[0][1]
                    if len(raw) > self.MAX_EMAIL_SIZE:
                        log.warning("email_too_large", size=len(raw))
                        conn.store(num, "+FLAGS", "\\Seen")
                        continue
                    offers = self._parse_email(raw)
                    for offer in offers:
                        await callback(offer)
                    conn.store(num, "+FLAGS", "\\Seen")
        finally:
            conn.logout()

    def _parse_email(self, raw: bytes) -> list[RawOffer]:
        msg = email.message_from_bytes(raw, policy=policy.default)
        received_at = str(msg.get("Date", ""))
        sender = str(msg.get("From", ""))
        subject = str(msg.get("Subject", ""))
        sender_email = self._extract_email_address(sender)
        sender_domain = sender_email.split("@")[-1] if sender_email else None
        offers = []

        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in disposition:
                filename = part.get_filename() or "attachment"
                payload = part.get_payload(decode=True) or b""
                fmt = self._detect_format_from_filename(filename)
                if fmt:
                    offers.append(RawOffer(
                        offer_id=self._new_id(),
                        channel=IngestionChannel.EMAIL_IMAP,
                        format=fmt,
                        raw_content=payload,
                        content_type=content_type,
                        filename=filename,
                        sender_email=sender_email,
                        sender_domain=sender_domain,
                        subject=subject,
                        received_at=received_at,
                        checksum_sha256=hashlib.sha256(payload).hexdigest(),
                        file_size_bytes=len(payload),
                    ))
            elif content_type == "text/html" and "attachment" not in disposition:
                payload = part.get_payload(decode=True) or b""
                offers.append(RawOffer(
                    offer_id=self._new_id(),
                    channel=IngestionChannel.EMAIL_IMAP,
                    format=OfferFormat.EMAIL_HTML,
                    raw_content=payload,
                    content_type="text/html",
                    filename=None,
                    sender_email=sender_email,
                    sender_domain=sender_domain,
                    subject=subject,
                    received_at=received_at,
                    checksum_sha256=hashlib.sha256(payload).hexdigest(),
                    file_size_bytes=len(payload),
                ))
            elif content_type == "text/plain" and "attachment" not in disposition:
                payload = part.get_payload(decode=True) or b""
                if len(payload) > 50:   # ignore trivial plaintext
                    offers.append(RawOffer(
                        offer_id=self._new_id(),
                        channel=IngestionChannel.EMAIL_IMAP,
                        format=OfferFormat.EMAIL_TEXT,
                        raw_content=payload,
                        content_type="text/plain",
                        filename=None,
                        sender_email=sender_email,
                        sender_domain=sender_domain,
                        subject=subject,
                        received_at=received_at,
                        checksum_sha256=hashlib.sha256(payload).hexdigest(),
                        file_size_bytes=len(payload),
                    ))

        return offers

    def _detect_format_from_filename(self, filename: str) -> Optional[OfferFormat]:
        ext = Path(filename).suffix.lower().lstrip(".")
        mapping = {
            "pdf": OfferFormat.PDF,
            "xlsx": OfferFormat.EXCEL, "xls": OfferFormat.EXCEL,
            "csv": OfferFormat.CSV,
            "docx": OfferFormat.WORD, "doc": OfferFormat.WORD,
            "xml": OfferFormat.PUNCHOUT_XML,
            "json": OfferFormat.JSON_API,
            "edi": OfferFormat.EDI_X12,
        }
        return mapping.get(ext)

    def _extract_email_address(self, from_header: str) -> Optional[str]:
        import re
        m = re.search(r"[\w.+-]+@[\w.-]+\.\w+", from_header)
        return m.group(0).lower() if m else None

    def _new_id(self) -> str:
        import uuid
        return str(uuid.uuid4())


class S3DropIngestor:
    """
    Watches S3 bucket prefix for new supplier offer files.
    Triggered by S3 Event Notifications → SQS queue.
    """
    def __init__(self, sqs_url: str, s3_bucket: str):
        self.sqs_url = sqs_url
        self.s3_bucket = s3_bucket

    async def run(self, callback):
        import aioboto3
        import json
        session = aioboto3.Session()
        while True:
            async with session.client("sqs") as sqs:
                resp = await sqs.receive_message(
                    QueueUrl=self.sqs_url,
                    MaxNumberOfMessages=10,
                    WaitTimeSeconds=20,
                )
                for msg in resp.get("Messages", []):
                    body = json.loads(msg["Body"])
                    for record in body.get("Records", []):
                        key = record["s3"]["object"]["key"]
                        size = record["s3"]["object"]["size"]
                        await self._process_s3_object(key, size, session, callback)
                    await sqs.delete_message(
                        QueueUrl=self.sqs_url,
                        ReceiptHandle=msg["ReceiptHandle"],
                    )

    async def _process_s3_object(self, key: str, size: int, session, callback):
        import hashlib
        async with session.client("s3") as s3:
            resp = await s3.get_object(Bucket=self.s3_bucket, Key=key)
            content = await resp["Body"].read()
        filename = Path(key).name
        fmt = EmailIMAPIngestor("", 0, "", "")._detect_format_from_filename(filename)
        if not fmt:
            return
        import uuid
        offer = RawOffer(
            offer_id=str(uuid.uuid4()),
            channel=IngestionChannel.S3_DROP,
            format=fmt,
            raw_content=content,
            content_type=resp.get("ContentType", "application/octet-stream"),
            filename=filename,
            sender_email=None,
            sender_domain=None,
            subject=None,
            received_at=str(resp["LastModified"]),
            checksum_sha256=hashlib.sha256(content).hexdigest(),
            file_size_bytes=size,
        )
        await callback(offer)
```

### 1.3 Document Converter — normalizacja do tekstu

```python
from abc import ABC, abstractmethod


class DocumentConverter(ABC):
    @abstractmethod
    async def convert(self, raw: RawOffer) -> OfferDocument:
        ...


class PDFConverter(DocumentConverter):
    async def convert(self, raw: RawOffer) -> OfferDocument:
        import fitz
        import io
        doc = fitz.open(stream=raw.raw_content, filetype="pdf")
        pages = []
        for page in doc:
            pages.append(page.get_text("text"))
        text = "\n".join(pages)
        quality = min(len(text) / max(doc.page_count * 200, 1), 1.0)
        return OfferDocument(
            offer_id=_new_id(), raw_offer_id=raw.offer_id,
            format=raw.format, channel=raw.channel,
            text_content=text, structured_data=None,
            pages=doc.page_count, language=_detect_language(text),
            encoding="utf-8", supplier_hint=raw.sender_domain,
            rfq_ref=_extract_rfq_ref(text), currency_hint=_detect_currency(text),
            extraction_quality=quality,
        )


class ExcelConverter(DocumentConverter):
    async def convert(self, raw: RawOffer) -> OfferDocument:
        import openpyxl
        import io
        wb = openpyxl.load_workbook(io.BytesIO(raw.raw_content), data_only=True)
        rows_data = []
        structured = {}
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            sheet_rows = []
            for row in ws.iter_rows(values_only=True):
                if any(c is not None for c in row):
                    sheet_rows.append([str(c) if c is not None else "" for c in row])
            structured[sheet_name] = sheet_rows
            # Flatten to text
            for row in sheet_rows:
                rows_data.append("\t".join(row))
        text = "\n".join(rows_data)
        return OfferDocument(
            offer_id=_new_id(), raw_offer_id=raw.offer_id,
            format=raw.format, channel=raw.channel,
            text_content=text, structured_data=structured,
            pages=len(wb.sheetnames), language=_detect_language(text),
            encoding="utf-8", supplier_hint=raw.sender_domain,
            rfq_ref=_extract_rfq_ref(text), currency_hint=_detect_currency(text),
            extraction_quality=0.95,
        )


class CSVConverter(DocumentConverter):
    async def convert(self, raw: RawOffer) -> OfferDocument:
        import csv
        import io
        text_raw = raw.raw_content.decode(_detect_encoding(raw.raw_content), errors="replace")
        reader = csv.DictReader(io.StringIO(text_raw))
        rows = list(reader)
        text = "\n".join("\t".join(str(v) for v in row.values()) for row in rows)
        return OfferDocument(
            offer_id=_new_id(), raw_offer_id=raw.offer_id,
            format=raw.format, channel=raw.channel,
            text_content=text, structured_data={"rows": rows, "headers": reader.fieldnames or []},
            pages=1, language=_detect_language(text),
            encoding=_detect_encoding(raw.raw_content),
            supplier_hint=raw.sender_domain,
            rfq_ref=_extract_rfq_ref(text), currency_hint=_detect_currency(text),
            extraction_quality=0.98,
        )


class HTMLConverter(DocumentConverter):
    async def convert(self, raw: RawOffer) -> OfferDocument:
        from bs4 import BeautifulSoup
        html = raw.raw_content.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return OfferDocument(
            offer_id=_new_id(), raw_offer_id=raw.offer_id,
            format=raw.format, channel=raw.channel,
            text_content=text, structured_data=None,
            pages=1, language=_detect_language(text),
            encoding="utf-8", supplier_hint=raw.sender_domain,
            rfq_ref=_extract_rfq_ref(text), currency_hint=_detect_currency(text),
            extraction_quality=0.85,
        )


class EDIConverter(DocumentConverter):
    """Converts EDI X12 855 / EDIFACT QUOTES to structured dict + readable text."""
    async def convert(self, raw: RawOffer) -> OfferDocument:
        text_raw = raw.raw_content.decode("ascii", errors="replace")
        if raw.format == OfferFormat.EDI_X12:
            structured = self._parse_x12(text_raw)
        else:
            structured = self._parse_edifact(text_raw)
        text = self._edi_to_text(structured)
        return OfferDocument(
            offer_id=_new_id(), raw_offer_id=raw.offer_id,
            format=raw.format, channel=raw.channel,
            text_content=text, structured_data=structured,
            pages=1, language="en", encoding="ascii",
            supplier_hint=raw.sender_domain,
            rfq_ref=structured.get("rfq_ref"),
            currency_hint=structured.get("currency"),
            extraction_quality=0.97,
        )

    def _parse_x12(self, text: str) -> dict:
        # Simplified X12 855 parser
        segments = text.strip().split("~")
        result = {"segments": [], "line_items": []}
        for seg in segments:
            elements = seg.strip().split("*")
            if not elements:
                continue
            seg_id = elements[0]
            result["segments"].append({"id": seg_id, "elements": elements[1:]})
            if seg_id == "PO1":   # Line item
                result["line_items"].append({
                    "position": elements[1] if len(elements) > 1 else "",
                    "quantity": elements[2] if len(elements) > 2 else "",
                    "uom": elements[3] if len(elements) > 3 else "",
                    "unit_price": elements[4] if len(elements) > 4 else "",
                    "item_id": elements[7] if len(elements) > 7 else "",
                })
        return result

    def _parse_edifact(self, text: str) -> dict:
        segments = text.strip().split("'")
        result = {"segments": [], "line_items": []}
        for seg in segments:
            elements = seg.strip().split("+")
            if elements:
                result["segments"].append({"id": elements[0], "elements": elements[1:]})
        return result

    def _edi_to_text(self, structured: dict) -> str:
        lines = []
        for item in structured.get("line_items", []):
            lines.append(f"Item: {item.get('item_id')} Qty: {item.get('quantity')} "
                         f"UOM: {item.get('uom')} Price: {item.get('unit_price')}")
        return "\n".join(lines)


class ConverterRegistry:
    _converters: dict[OfferFormat, DocumentConverter] = {
        OfferFormat.PDF:          PDFConverter(),
        OfferFormat.EXCEL:        ExcelConverter(),
        OfferFormat.CSV:          CSVConverter(),
        OfferFormat.EMAIL_HTML:   HTMLConverter(),
        OfferFormat.EMAIL_TEXT:   HTMLConverter(),   # reuse stripped text
        OfferFormat.EDI_X12:      EDIConverter(),
        OfferFormat.EDI_EDIFACT:  EDIConverter(),
    }

    def get(self, fmt: OfferFormat) -> DocumentConverter:
        conv = self._converters.get(fmt)
        if not conv:
            raise UnsupportedFormatError(f"No converter for: {fmt}")
        return conv


# ── helpers ───────────────────────────────────────────────────────────────────

import re
import uuid

def _new_id() -> str:
    return str(uuid.uuid4())

def _detect_language(text: str) -> str:
    try:
        from langdetect import detect
        return detect(text[:1000]) or "en"
    except Exception:
        return "en"

def _detect_currency(text: str) -> Optional[str]:
    patterns = [
        (r"\bEUR\b|€", "EUR"),
        (r"\bUSD\b|\$", "USD"),
        (r"\bGBP\b|£", "GBP"),
        (r"\bPLN\b|zł", "PLN"),
        (r"\bCNY\b|¥|RMB", "CNY"),
        (r"\bINR\b|₹", "INR"),
    ]
    for pattern, currency in patterns:
        if re.search(pattern, text[:2000]):
            return currency
    return None

def _extract_rfq_ref(text: str) -> Optional[str]:
    m = re.search(
        r"(?:RFQ|anfrage|запрос|inquiry|ref(?:erence)?)[:\s#-]+([A-Z0-9\-_.]{4,30})",
        text, re.IGNORECASE,
    )
    return m.group(1).strip() if m else None

def _detect_encoding(content: bytes) -> str:
    try:
        import chardet
        result = chardet.detect(content[:4096])
        return result.get("encoding") or "utf-8"
    except Exception:
        return "utf-8"
```

---

## 2. NLP Pipeline

### 2.1 Architektura pipeline'u

```python
from dataclasses import dataclass, field
from typing import Any
import time
import structlog

log = structlog.get_logger()


@dataclass
class NLPContext:
    """Shared context passing through all NLP stages."""
    doc: OfferDocument
    tokens: list["Token"] = field(default_factory=list)
    sentences: list[str] = field(default_factory=list)
    entities: list["ExtractedEntity"] = field(default_factory=list)
    price_candidates: list["PriceCandidate"] = field(default_factory=list)
    line_items: list["OfferLineItem"] = field(default_factory=list)
    supplier_id: Optional[str] = None
    normalized_currency: str = "EUR"
    stage_timings: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Token:
    text: str
    lemma: str
    pos: str           # NOUN / NUM / CURR / SYM / ...
    tag: str
    idx: int           # character offset in text
    is_numeric: bool
    is_currency: bool


class NLPPipeline:
    """
    Sequential NLP pipeline for supplier offer documents.
    Stages: tokenize → normalize → NER → price_extract → unit_resolve → line_group
    """
    STAGES = [
        "tokenize",
        "sentence_split",
        "normalize_text",
        "ner_extract",
        "price_extract",
        "unit_resolve",
        "material_match",
        "line_item_group",
    ]

    def __init__(
        self,
        tokenizer: "MultilingualTokenizer",
        ner_engine: "NEREngine",
        price_extractor: "PriceExtractor",
        unit_resolver: "UnitConversionEngine",
        material_matcher: "MaterialMatcher",
        line_grouper: "LineItemGrouper",
    ):
        self.tokenizer       = tokenizer
        self.ner_engine      = ner_engine
        self.price_extractor = price_extractor
        self.unit_resolver   = unit_resolver
        self.material_matcher = material_matcher
        self.line_grouper    = line_grouper

    async def process(self, doc: OfferDocument) -> NLPContext:
        ctx = NLPContext(doc=doc, normalized_currency=doc.currency_hint or "EUR")
        fns = {
            "tokenize":        self._tokenize,
            "sentence_split":  self._sentence_split,
            "normalize_text":  self._normalize_text,
            "ner_extract":     self._ner_extract,
            "price_extract":   self._price_extract,
            "unit_resolve":    self._unit_resolve,
            "material_match":  self._material_match,
            "line_item_group": self._line_item_group,
        }
        for stage in self.STAGES:
            t0 = time.monotonic()
            try:
                await fns[stage](ctx)
            except RecoverableNLPError as e:
                ctx.warnings.append(f"[{stage}] {e}")
                log.warning("nlp_stage_degraded", stage=stage, error=str(e))
            except Exception as e:
                ctx.warnings.append(f"[{stage}] UNEXPECTED: {e}")
                log.error("nlp_stage_error", stage=stage, error=str(e), exc_info=True)
            finally:
                ctx.stage_timings[stage] = (time.monotonic() - t0) * 1000

        return ctx

    async def _tokenize(self, ctx: NLPContext):
        ctx.tokens = await self.tokenizer.tokenize(ctx.doc.text_content, ctx.doc.language)

    async def _sentence_split(self, ctx: NLPContext):
        ctx.sentences = await self.tokenizer.split_sentences(ctx.doc.text_content, ctx.doc.language)

    async def _normalize_text(self, ctx: NLPContext):
        ctx.doc.text_content = TextNormalizer.normalize(ctx.doc.text_content, ctx.doc.language)

    async def _ner_extract(self, ctx: NLPContext):
        ctx.entities = await self.ner_engine.extract(ctx.doc.text_content, ctx.doc.language)

    async def _price_extract(self, ctx: NLPContext):
        ctx.price_candidates = await self.price_extractor.extract(ctx.doc, ctx.entities)

    async def _unit_resolve(self, ctx: NLPContext):
        for candidate in ctx.price_candidates:
            candidate = await self.unit_resolver.resolve(candidate, ctx.normalized_currency)

    async def _material_match(self, ctx: NLPContext):
        for entity in ctx.entities:
            if entity.entity_type == EntityType.MATERIAL:
                entity.resolved_material = await self.material_matcher.match(entity.text)

    async def _line_item_group(self, ctx: NLPContext):
        ctx.line_items = await self.line_grouper.group(
            ctx.price_candidates, ctx.entities, ctx.doc
        )
```

### 2.2 Multilingual Tokenizer

```python
class MultilingualTokenizer:
    """
    spaCy-based multilingual tokenizer.
    Models: en_core_web_lg, de_core_news_lg, pl_core_news_lg,
            zh_core_web_lg, ru_core_news_lg.
    Fallback: xx_ent_wiki_sm (multilingual).
    """

    MODELS = {
        "en": "en_core_web_lg",
        "de": "de_core_news_lg",
        "pl": "pl_core_news_lg",
        "zh": "zh_core_web_lg",
        "ru": "ru_core_news_lg",
        "fr": "fr_core_news_lg",
        "it": "it_core_news_lg",
        "es": "es_core_news_lg",
    }
    FALLBACK_MODEL = "xx_ent_wiki_sm"

    def __init__(self):
        import spacy
        self._nlp_cache: dict[str, Any] = {}
        self._load_fallback(spacy)

    def _load_fallback(self, spacy):
        try:
            self._nlp_cache["xx"] = spacy.load(self.FALLBACK_MODEL)
        except OSError:
            pass

    def _get_nlp(self, lang: str):
        import spacy
        if lang not in self._nlp_cache:
            model = self.MODELS.get(lang, self.FALLBACK_MODEL)
            try:
                self._nlp_cache[lang] = spacy.load(model)
            except OSError:
                log.warning("spacy_model_missing", model=model, lang=lang)
                self._nlp_cache[lang] = self._nlp_cache.get("xx")
        return self._nlp_cache[lang]

    async def tokenize(self, text: str, lang: str) -> list[Token]:
        nlp = self._get_nlp(lang)
        if not nlp:
            return []
        doc = nlp(text[:1_000_000])   # spaCy limit guard
        tokens = []
        for t in doc:
            tokens.append(Token(
                text=t.text, lemma=t.lemma_, pos=t.pos_, tag=t.tag_,
                idx=t.idx,
                is_numeric=t.like_num or t.pos_ == "NUM",
                is_currency=t.is_currency or t.text in ("€", "$", "£", "¥", "zł"),
            ))
        return tokens

    async def split_sentences(self, text: str, lang: str) -> list[str]:
        nlp = self._get_nlp(lang)
        if not nlp:
            return text.split("\n")
        doc = nlp(text[:1_000_000])
        return [sent.text.strip() for sent in doc.sents if sent.text.strip()]


class TextNormalizer:
    """Normalizes whitespace, Unicode, number formats for consistent regex matching."""

    UNICODE_SUBS = {
        "’": "'", "“": '"', "”": '"',  # curly quotes
        "–": "-", "—": "-",                   # en/em dash
        " ": " ",                                  # non-breaking space
        " ": " ",                                  # narrow no-break space
        "…": "...",                                # ellipsis
    }

    @classmethod
    def normalize(cls, text: str, lang: str = "en") -> str:
        for src, tgt in cls.UNICODE_SUBS.items():
            text = text.replace(src, tgt)
        # Normalize number formats: 1.234,56 → 1234.56 (DE/PL style)
        if lang in ("de", "pl", "fr", "it", "es"):
            text = cls._normalize_european_numbers(text)
        # Collapse whitespace
        import re
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @classmethod
    def _normalize_european_numbers(cls, text: str) -> str:
        import re
        # 1.234,56 € → 1234.56 EUR
        def replace_eu_num(m):
            num = m.group(0).replace(".", "").replace(",", ".")
            return num
        return re.sub(r"\b\d{1,3}(?:\.\d{3})+,\d+\b", replace_eu_num, text)
```

---

## 3. Entity Extraction

### 3.1 Entity domain model

```python
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class EntityType(str, Enum):
    PRICE           = "PRICE"           # Numeric price value
    CURRENCY        = "CURRENCY"        # EUR, USD, PLN, ...
    QUANTITY        = "QUANTITY"        # 100, 500 pcs
    UNIT            = "UNIT"            # pcs, kg, m, ...
    MATERIAL        = "MATERIAL"        # S235JR, Aluminium 6061, PA66
    PART_NUMBER     = "PART_NUMBER"     # Supplier or customer part number
    LEAD_TIME       = "LEAD_TIME"       # 4 weeks, 30 days
    VALIDITY        = "VALIDITY"        # Quote valid until 2026-09-30
    INCOTERM        = "INCOTERM"        # EXW, DDP, FCA
    PAYMENT_TERM    = "PAYMENT_TERM"    # NET 30, 30 days
    SUPPLIER_NAME   = "SUPPLIER_NAME"
    CONTACT_PERSON  = "CONTACT_PERSON"
    RFQ_REF         = "RFQ_REF"
    DISCOUNT        = "DISCOUNT"        # 5%, 10 EUR/pc rebate
    MOQ             = "MOQ"             # Minimum order quantity
    PACKAGING       = "PACKAGING"       # Bulk, reel 3000pcs
    TOOLING_COST    = "TOOLING_COST"    # One-time tooling charge
    SURFACE_FINISH  = "SURFACE_FINISH"
    CERTIFICATION   = "CERTIFICATION"   # RoHS, REACH, ISO 9001


@dataclass
class ExtractedEntity:
    entity_id: str
    entity_type: EntityType
    text: str                           # raw text from document
    normalized_value: Optional[str]     # cleaned/normalized form
    start: int                          # char offset
    end: int
    confidence: float
    sentence_idx: int
    source: str                         # NER_MODEL / REGEX / STRUCTURED
    resolved_material: Optional[dict] = None   # from MaterialMatcher
    metadata: dict = field(default_factory=dict)
```

### 3.2 NER Engine — spaCy + Regex hybrid

```python
import re
from abc import ABC, abstractmethod


class NERBackend(ABC):
    @abstractmethod
    async def extract(self, text: str, lang: str) -> list[ExtractedEntity]:
        ...


class SpacyNERBackend(NERBackend):
    """
    spaCy NER for: ORG (supplier name), PERSON (contact), DATE (validity),
    CARDINAL (quantities), MONEY (price + currency).
    Custom entity ruler for domain terms: incoterms, payment terms, certifications.
    """

    SPACY_TYPE_MAP = {
        "ORG":     EntityType.SUPPLIER_NAME,
        "PERSON":  EntityType.CONTACT_PERSON,
        "DATE":    EntityType.VALIDITY,
        "CARDINAL": EntityType.QUANTITY,
        "MONEY":   EntityType.PRICE,
    }

    CUSTOM_PATTERNS = [
        # Incoterms
        {"label": "INCOTERM", "pattern": [{"TEXT": {"REGEX": "^(EXW|FCA|CPT|CIP|DAP|DPU|DDP|FAS|FOB|CFR|CIF)$"}}]},
        # Payment terms
        {"label": "PAYMENT_TERM", "pattern": [{"TEXT": {"REGEX": "^NET$"}}, {"TEXT": {"REGEX": "^\\d{1,3}$"}}]},
        {"label": "PAYMENT_TERM", "pattern": [{"TEXT": {"REGEX": "^\\d{1,3}$"}}, {"LOWER": "days"}]},
        # Certifications
        {"label": "CERTIFICATION", "pattern": [{"TEXT": {"REGEX": "^(RoHS|REACH|ISO|IATF|AS9100)$"}}]},
    ]

    def __init__(self, tokenizer: MultilingualTokenizer):
        self.tokenizer = tokenizer
        self._setup_ruler()

    def _setup_ruler(self):
        import spacy
        # Will be applied to each language's pipeline
        self._patterns = self.CUSTOM_PATTERNS

    async def extract(self, text: str, lang: str) -> list[ExtractedEntity]:
        nlp = self.tokenizer._get_nlp(lang)
        if not nlp:
            return []
        doc = nlp(text[:500_000])
        entities = []
        import uuid
        for ent in doc.ents:
            etype = self.SPACY_TYPE_MAP.get(ent.label_)
            if not etype:
                label_lower = ent.label_.lower()
                if "incoterm" in label_lower:
                    etype = EntityType.INCOTERM
                elif "payment" in label_lower:
                    etype = EntityType.PAYMENT_TERM
                elif "cert" in label_lower:
                    etype = EntityType.CERTIFICATION
                else:
                    continue
            entities.append(ExtractedEntity(
                entity_id=str(uuid.uuid4()),
                entity_type=etype,
                text=ent.text,
                normalized_value=ent.text.strip(),
                start=ent.start_char, end=ent.end_char,
                confidence=0.80,
                sentence_idx=0,
                source="NER_MODEL",
            ))
        return entities


class RegexNERBackend(NERBackend):
    """
    Regex-based extraction for structured entities:
    prices, units, part numbers, lead times, MOQ, discounts.
    Handles multilingual number formats (EN, DE, PL, ZH).
    """

    # Price: 12.50 EUR / piece  |  € 1.234,56  |  USD 0.089
    PRICE_RE = re.compile(
        r"(?P<curr_pre>[€$£¥₹]|EUR|USD|GBP|PLN|CNY|INR|CZK|HUF|TRY|MXN|BRL)?\s*"
        r"(?P<value>\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
        r"\s*(?P<curr_post>EUR|USD|GBP|PLN|CNY|INR|CZK|HUF|TRY|MXN|BRL|[€$£¥₹])?"
        r"(?:\s*/\s*(?P<per_unit>\w+))?",
        re.IGNORECASE,
    )

    # Part numbers: alphanumeric with hyphens/dots, ≥5 chars
    PART_RE = re.compile(
        r"(?:part\s*(?:no|nr|number|#)|position|pos\.|artikel|art\.)[:\s#]*"
        r"([A-Z0-9][A-Z0-9\-_.]{3,29})",
        re.IGNORECASE,
    )

    # Lead time: 4 weeks, 6-8 weeks, 30 days, 3 months
    LEAD_RE = re.compile(
        r"(?:lead\s*time|lieferzeit|délai|срок|dostawa)[:\s]*"
        r"(\d+(?:\s*[-–]\s*\d+)?)\s*(week|woche|jour|день|tydzień|day|month|monat)s?",
        re.IGNORECASE,
    )

    # MOQ: MOQ 500 pcs, minimum order: 1000
    MOQ_RE = re.compile(
        r"(?:MOQ|min(?:imum)?\s*order(?:\s*qty)?|mindestbestellmenge)[:\s]*"
        r"(\d[\d,.]*)\s*(\w+)?",
        re.IGNORECASE,
    )

    # Validity: valid until / gültig bis / valable jusqu'au
    VALIDITY_RE = re.compile(
        r"(?:valid(?:ity)?\s*(?:until|till|through)|gültig\s*bis|valable\s*jusqu['\s]au"
        r"|действительно\s*до)[:\s]*"
        r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})",
        re.IGNORECASE,
    )

    # Discount: 5%, -10%, 2% discount
    DISCOUNT_RE = re.compile(
        r"(?:discount|rabatt|remise|скидка|rabat)[:\s]*(-?\d+(?:[.,]\d+)?)\s*%"
        r"|(-?\d+(?:[.,]\d+)?)\s*%\s*(?:discount|rabatt|remise|скидка|rabat)",
        re.IGNORECASE,
    )

    # Tooling: tooling cost 2500 EUR, Werkzeugkosten
    TOOLING_RE = re.compile(
        r"(?:tooling|werkzeug(?:kosten)?|mould(?:ing)?|die\s*cost)[:\s]*"
        r"(?:[€$£]?\s*)(\d[\d,.]*)\s*(EUR|USD|GBP|PLN|[€$£])?",
        re.IGNORECASE,
    )

    # Quantity with unit: 500 pcs, 100 kg, 50 m
    QTY_RE = re.compile(
        r"\b(\d[\d,.]*)\s*(pcs?|pieces?|stück|szt|units?|kg|g|lbs?|oz|"
        r"m\b|mm\b|cm\b|ft\b|in\b|l\b|ml\b|gal\b|rolls?|reels?|sets?|pairs?)",
        re.IGNORECASE,
    )

    async def extract(self, text: str, lang: str) -> list[ExtractedEntity]:
        import uuid
        entities = []

        # Part numbers
        for m in self.PART_RE.finditer(text):
            entities.append(ExtractedEntity(
                entity_id=str(uuid.uuid4()),
                entity_type=EntityType.PART_NUMBER,
                text=m.group(0), normalized_value=m.group(1).upper().strip(),
                start=m.start(), end=m.end(),
                confidence=0.85, sentence_idx=0, source="REGEX",
            ))

        # Lead time
        for m in self.LEAD_RE.finditer(text):
            value = m.group(1).strip()
            unit  = m.group(2).lower()
            normalized = f"{value} {unit}s"
            entities.append(ExtractedEntity(
                entity_id=str(uuid.uuid4()),
                entity_type=EntityType.LEAD_TIME,
                text=m.group(0), normalized_value=normalized,
                start=m.start(), end=m.end(),
                confidence=0.88, sentence_idx=0, source="REGEX",
            ))

        # MOQ
        for m in self.MOQ_RE.finditer(text):
            entities.append(ExtractedEntity(
                entity_id=str(uuid.uuid4()),
                entity_type=EntityType.MOQ,
                text=m.group(0), normalized_value=m.group(1).replace(",", "").replace(".", ""),
                start=m.start(), end=m.end(),
                confidence=0.87, sentence_idx=0, source="REGEX",
                metadata={"unit": m.group(2) or "pcs"},
            ))

        # Validity date
        for m in self.VALIDITY_RE.finditer(text):
            entities.append(ExtractedEntity(
                entity_id=str(uuid.uuid4()),
                entity_type=EntityType.VALIDITY,
                text=m.group(0), normalized_value=m.group(1),
                start=m.start(), end=m.end(),
                confidence=0.90, sentence_idx=0, source="REGEX",
            ))

        # Discount
        for m in self.DISCOUNT_RE.finditer(text):
            pct = m.group(1) or m.group(2) or "0"
            entities.append(ExtractedEntity(
                entity_id=str(uuid.uuid4()),
                entity_type=EntityType.DISCOUNT,
                text=m.group(0), normalized_value=pct.replace(",", "."),
                start=m.start(), end=m.end(),
                confidence=0.85, sentence_idx=0, source="REGEX",
            ))

        # Tooling cost
        for m in self.TOOLING_RE.finditer(text):
            entities.append(ExtractedEntity(
                entity_id=str(uuid.uuid4()),
                entity_type=EntityType.TOOLING_COST,
                text=m.group(0), normalized_value=m.group(1).replace(",", ""),
                start=m.start(), end=m.end(),
                confidence=0.82, sentence_idx=0, source="REGEX",
                metadata={"currency": m.group(2) or ""},
            ))

        # Quantity + unit
        for m in self.QTY_RE.finditer(text):
            entities.append(ExtractedEntity(
                entity_id=str(uuid.uuid4()),
                entity_type=EntityType.QUANTITY,
                text=m.group(0),
                normalized_value=m.group(1).replace(",", "").replace(".", ""),
                start=m.start(), end=m.end(),
                confidence=0.80, sentence_idx=0, source="REGEX",
                metadata={"unit_raw": m.group(2).lower()},
            ))

        return entities


class NEREngine:
    """
    Merges SpacyNER + RegexNER, resolves conflicts by confidence ranking.
    """
    def __init__(self, spacy_backend: SpacyNERBackend, regex_backend: RegexNERBackend):
        self.spacy = spacy_backend
        self.regex = regex_backend

    async def extract(self, text: str, lang: str) -> list[ExtractedEntity]:
        spacy_entities = await self.spacy.extract(text, lang)
        regex_entities = await self.regex.extract(text, lang)
        merged = self._merge(spacy_entities, regex_entities)
        return sorted(merged, key=lambda e: e.start)

    def _merge(self, spacy: list, regex: list) -> list:
        """Remove overlapping entities, prefer higher confidence."""
        all_entities = spacy + regex
        all_entities.sort(key=lambda e: (e.start, -e.confidence))
        result = []
        last_end = -1
        for ent in all_entities:
            if ent.start >= last_end:
                result.append(ent)
                last_end = ent.end
            elif ent.confidence > (result[-1].confidence if result else 0):
                result[-1] = ent
                last_end = ent.end
        return result
```

### 3.3 Structured data extractor (Excel / CSV / EDI)

```python
class StructuredOfferExtractor:
    """
    Extracts line items from structured formats (Excel, CSV, EDI)
    using header column matching — no NLP needed.
    """

    PRICE_COL_ALIASES = [
        "price", "unit price", "preis", "cena", "価格", "цена",
        "unit_price", "price_per_unit", "unitprice", "einzelpreis",
        "net price", "netto", "cost",
    ]
    QTY_COL_ALIASES = [
        "qty", "quantity", "menge", "ilość", "数量", "количество",
        "order qty", "bestellmenge",
    ]
    PART_COL_ALIASES = [
        "part number", "part no", "part_no", "article", "artikelnummer",
        "item", "material", "sku", "ref", "position",
    ]
    DESC_COL_ALIASES = [
        "description", "bezeichnung", "opis", "描述", "наименование",
        "item description", "material description",
    ]
    UOM_COL_ALIASES = [
        "uom", "unit", "einheit", "jednostka", "单位",
        "unit of measure", "me",
    ]
    CURRENCY_COL_ALIASES = [
        "currency", "währung", "waluta", "货币", "валюта",
    ]

    async def extract(self, doc: OfferDocument) -> list["OfferLineItem"]:
        if doc.structured_data is None:
            return []
        if doc.format == OfferFormat.EXCEL:
            return self._from_excel(doc.structured_data)
        if doc.format == OfferFormat.CSV:
            return self._from_csv(doc.structured_data)
        if doc.format in (OfferFormat.EDI_X12, OfferFormat.EDI_EDIFACT):
            return self._from_edi(doc.structured_data)
        return []

    def _from_excel(self, data: dict) -> list["OfferLineItem"]:
        items = []
        for sheet_name, rows in data.items():
            if not rows:
                continue
            headers = [str(h).lower().strip() for h in rows[0]]
            col_map = self._map_columns(headers)
            for row in rows[1:]:
                item = self._row_to_item(row, col_map, headers)
                if item:
                    items.append(item)
        return items

    def _from_csv(self, data: dict) -> list["OfferLineItem"]:
        items = []
        rows = data.get("rows", [])
        headers_raw = data.get("headers") or (list(rows[0].keys()) if rows else [])
        headers = [str(h).lower().strip() for h in headers_raw]
        col_map = self._map_columns(headers)
        for row in rows:
            values = list(row.values())
            item = self._row_to_item(values, col_map, headers)
            if item:
                items.append(item)
        return items

    def _from_edi(self, data: dict) -> list["OfferLineItem"]:
        items = []
        for li in data.get("line_items", []):
            try:
                price = float(li.get("unit_price", "0").replace(",", "."))
                qty   = float(li.get("quantity", "1").replace(",", "."))
            except (ValueError, AttributeError):
                continue
            items.append(OfferLineItem(
                line_id=_new_id(),
                position=li.get("position", ""),
                part_number_supplier=li.get("item_id", ""),
                description="",
                quantity=qty,
                uom=li.get("uom", "pcs"),
                unit_price_raw=price,
                currency_raw="USD",   # X12 default
                confidence=0.90,
                source="EDI",
            ))
        return items

    def _map_columns(self, headers: list[str]) -> dict[str, int]:
        mapping = {}
        for field_name, aliases in [
            ("price",    self.PRICE_COL_ALIASES),
            ("qty",      self.QTY_COL_ALIASES),
            ("part",     self.PART_COL_ALIASES),
            ("desc",     self.DESC_COL_ALIASES),
            ("uom",      self.UOM_COL_ALIASES),
            ("currency", self.CURRENCY_COL_ALIASES),
        ]:
            for alias in aliases:
                for idx, header in enumerate(headers):
                    if alias in header or header in alias:
                        if field_name not in mapping:
                            mapping[field_name] = idx
                        break
        return mapping

    def _row_to_item(self, row: list, col_map: dict, headers: list) -> Optional["OfferLineItem"]:
        def _get(field: str):
            idx = col_map.get(field)
            if idx is not None and idx < len(row):
                return str(row[idx]).strip()
            return None

        price_raw = _get("price")
        if not price_raw:
            return None
        try:
            price = float(price_raw.replace(",", ".").replace(" ", ""))
        except ValueError:
            return None
        if price <= 0:
            return None

        qty_raw = _get("qty") or "1"
        try:
            qty = float(qty_raw.replace(",", ".").replace(" ", ""))
        except ValueError:
            qty = 1.0

        return OfferLineItem(
            line_id=_new_id(),
            position=_get("part") or "",
            part_number_supplier=_get("part") or "",
            description=_get("desc") or "",
            quantity=qty,
            uom=_get("uom") or "pcs",
            unit_price_raw=price,
            currency_raw=_get("currency") or "EUR",
            confidence=0.92,
            source="STRUCTURED",
        )


@dataclass
class OfferLineItem:
    line_id: str
    position: str
    part_number_supplier: str
    description: str
    quantity: float
    uom: str
    unit_price_raw: float
    currency_raw: str
    unit_price_eur: Optional[float] = None
    uom_normalized: Optional[str] = None
    material_match: Optional[dict] = None
    bom_line_id: Optional[str] = None
    lead_time_days: Optional[int] = None
    moq: Optional[float] = None
    tooling_cost_eur: Optional[float] = None
    discount_pct: Optional[float] = None
    confidence: float = 0.0
    source: str = ""
    warnings: list[str] = field(default_factory=list)
```

---

## 4. Price Normalization

### 4.1 Price Extractor

```python
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field
from typing import Optional
import re


@dataclass
class PriceCandidate:
    price_id: str
    raw_text: str
    numeric_value: Decimal
    currency_raw: str
    currency_normalized: str       # ISO 4217
    per_unit_raw: Optional[str]    # "pcs", "kg", "100 pcs"
    price_type: str                # UNIT / TOTAL / TOOLING / SETUP
    quantity_break: Optional[Decimal]   # price-break quantity
    confidence: float
    start: int
    end: int
    context: str                   # surrounding sentence
    unit_price_eur: Optional[Decimal] = None


class PriceExtractor:
    """
    Extracts price candidates from both text (regex) and structured data.
    Handles: unit prices, price breaks, total prices, tooling costs.
    Distinguishes per-piece vs per-kg vs per-100-pcs pricing.
    """

    # Core price pattern — handles EN and DE/PL number formats
    PRICE_PATTERN = re.compile(
        r"(?P<curr_pre>EUR|USD|GBP|PLN|CNY|CZK|HUF|TRY|INR|[€$£¥₹])\s*"
        r"(?P<value>\d{1,3}(?:[',\s]\d{3})*(?:[.,]\d+)?)\b"
        r"|"
        r"(?P<value2>\d{1,3}(?:[',\s]\d{3})*(?:[.,]\d+)?)\s*"
        r"(?P<curr_post>EUR|USD|GBP|PLN|CNY|CZK|HUF|TRY|INR|[€$£¥₹])",
        re.IGNORECASE,
    )

    # Price break: "500+ pcs: 0.85 EUR"  |  "≥1000: 0.72"
    PRICE_BREAK_PATTERN = re.compile(
        r"(?P<qty>\d[\d,.']*)\s*(?:\+|≥|>=|and above|und mehr)?\s*"
        r"(?:pcs?|stk|szt|units?)?\s*[:\-]\s*"
        r"(?P<curr>[€$£]|EUR|USD|GBP|PLN)?\s*"
        r"(?P<price>\d+(?:[.,]\d+)?)",
        re.IGNORECASE,
    )

    # Total value: "Total: EUR 15,000.00"
    TOTAL_PATTERN = re.compile(
        r"(?:total|gesamt|suma|итого)[:\s]*"
        r"(?P<curr>[€$£]|EUR|USD|GBP|PLN)?\s*"
        r"(?P<value>\d{1,3}(?:[,.\s]\d{3})*(?:[.,]\d+)?)",
        re.IGNORECASE,
    )

    CURRENCY_SYMBOLS = {
        "€": "EUR", "$": "USD", "£": "GBP", "¥": "CNY", "₹": "INR", "zł": "PLN",
    }

    async def extract(self, doc: OfferDocument, entities: list[ExtractedEntity]) -> list[PriceCandidate]:
        candidates = []
        text = doc.text_content
        currency_context = self._infer_currency(doc, entities)

        # 1. Unit prices from regex
        for m in self.PRICE_PATTERN.finditer(text):
            raw_curr = m.group("curr_pre") or m.group("curr_post") or currency_context
            value_str = (m.group("value") or m.group("value2") or "0").strip()
            value = self._parse_number(value_str, doc.language)
            if value is None or value <= 0:
                continue
            curr_norm = self._normalize_currency(raw_curr)
            context = self._get_context(text, m.start(), m.end())
            price_type = self._classify_price_type(context)

            candidate = PriceCandidate(
                price_id=_new_id(),
                raw_text=m.group(0),
                numeric_value=value,
                currency_raw=raw_curr,
                currency_normalized=curr_norm,
                per_unit_raw=self._extract_per_unit(context),
                price_type=price_type,
                quantity_break=None,
                confidence=self._score_confidence(value, curr_norm, context),
                start=m.start(), end=m.end(),
                context=context,
            )
            candidates.append(candidate)

        # 2. Price breaks
        for m in self.PRICE_BREAK_PATTERN.finditer(text):
            qty_str   = m.group("qty").replace(",", "").replace("'", "").replace(" ", "")
            price_str = m.group("price").replace(",", ".")
            curr_raw  = m.group("curr") or currency_context
            try:
                qty   = Decimal(qty_str)
                price = Decimal(price_str)
            except InvalidOperation:
                continue
            candidates.append(PriceCandidate(
                price_id=_new_id(),
                raw_text=m.group(0),
                numeric_value=price,
                currency_raw=curr_raw,
                currency_normalized=self._normalize_currency(curr_raw),
                per_unit_raw="pcs",
                price_type="UNIT",
                quantity_break=qty,
                confidence=0.85,
                start=m.start(), end=m.end(),
                context=self._get_context(text, m.start(), m.end()),
            ))

        # 3. Total price
        for m in self.TOTAL_PATTERN.finditer(text):
            value_str = m.group("value").replace(",", "").replace(".", ".") \
                         if doc.language == "en" else m.group("value")
            value = self._parse_number(value_str, doc.language)
            if value is None:
                continue
            curr_raw = m.group("curr") or currency_context
            candidates.append(PriceCandidate(
                price_id=_new_id(),
                raw_text=m.group(0),
                numeric_value=value,
                currency_raw=curr_raw,
                currency_normalized=self._normalize_currency(curr_raw),
                per_unit_raw=None,
                price_type="TOTAL",
                quantity_break=None,
                confidence=0.88,
                start=m.start(), end=m.end(),
                context=self._get_context(text, m.start(), m.end()),
            ))

        return self._filter_outliers(candidates)

    def _parse_number(self, text: str, lang: str) -> Optional[Decimal]:
        """Handle both EN (1,234.56) and DE/PL (1.234,56) formats."""
        text = text.strip().replace(" ", "").replace("'", "")
        if not text:
            return None
        try:
            # European format: last comma is decimal separator
            if lang in ("de", "pl", "fr", "it", "es", "ru"):
                # 1.234,56 → 1234.56
                if "," in text and "." in text:
                    # 1.234,56 style
                    text = text.replace(".", "").replace(",", ".")
                elif "," in text and "." not in text:
                    text = text.replace(",", ".")
            else:
                # EN format: comma is thousands separator
                if "," in text and "." in text:
                    text = text.replace(",", "")
                elif "," in text:
                    # Could be decimal (0,85) — check if decimal part is ≤ 2 digits
                    parts = text.split(",")
                    if len(parts) == 2 and len(parts[1]) <= 2:
                        text = ".".join(parts)
                    else:
                        text = text.replace(",", "")
            return Decimal(text)
        except InvalidOperation:
            return None

    def _normalize_currency(self, raw: Optional[str]) -> str:
        if not raw:
            return "EUR"
        raw = raw.strip()
        if raw in self.CURRENCY_SYMBOLS:
            return self.CURRENCY_SYMBOLS[raw]
        return raw.upper()[:3]

    def _get_context(self, text: str, start: int, end: int, window: int = 100) -> str:
        return text[max(0, start - window): min(len(text), end + window)]

    def _classify_price_type(self, context: str) -> str:
        ctx = context.lower()
        if any(kw in ctx for kw in ["tooling", "werkzeug", "mould", "die cost"]):
            return "TOOLING"
        if any(kw in ctx for kw in ["setup", "rüstkosten", "einrichtung"]):
            return "SETUP"
        if any(kw in ctx for kw in ["total", "gesamt", "suma"]):
            return "TOTAL"
        return "UNIT"

    def _extract_per_unit(self, context: str) -> Optional[str]:
        m = re.search(
            r"(?:per|/|je|na)\s*(pcs?|piece|stück|szt|kg|g|m\b|100\s*pcs?|1000\s*pcs?)",
            context, re.IGNORECASE,
        )
        return m.group(1).lower() if m else None

    def _infer_currency(self, doc: OfferDocument, entities: list[ExtractedEntity]) -> str:
        for ent in entities:
            if ent.entity_type == EntityType.CURRENCY and ent.normalized_value:
                return ent.normalized_value.upper()
        return doc.currency_hint or "EUR"

    def _score_confidence(self, value: Decimal, currency: str, context: str) -> float:
        score = 0.70
        if currency in ("EUR", "USD", "GBP"):
            score += 0.10
        if 0.001 <= value <= 100_000:
            score += 0.10
        if any(kw in context.lower() for kw in ["unit price", "stückpreis", "cena jednostkowa"]):
            score += 0.10
        return min(score, 0.98)

    def _filter_outliers(self, candidates: list[PriceCandidate]) -> list[PriceCandidate]:
        """Remove prices that are statistical outliers (IQR method) for UNIT prices."""
        unit_prices = [float(c.numeric_value) for c in candidates if c.price_type == "UNIT"]
        if len(unit_prices) < 4:
            return candidates
        unit_prices_sorted = sorted(unit_prices)
        n = len(unit_prices_sorted)
        q1 = unit_prices_sorted[n // 4]
        q3 = unit_prices_sorted[3 * n // 4]
        iqr = q3 - q1
        lo, hi = q1 - 3 * iqr, q3 + 3 * iqr
        result = []
        for c in candidates:
            if c.price_type != "UNIT":
                result.append(c)
            elif lo <= float(c.numeric_value) <= hi:
                result.append(c)
            else:
                c.confidence = max(0, c.confidence - 0.30)
                c.price_type = "OUTLIER"
                result.append(c)
        return result
```
