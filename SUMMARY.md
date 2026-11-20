# Product-Level Sustainability Claim Verifier: summary

**Team / author:** [your name]  |  **Repository:** [repository URL]  |  **Demo video:** [video link]

## Problem

Retailers make sustainability claims about origin, packaging and carbon, but cannot back them up at product level. The evidence sits with suppliers across several tiers as certificates, invoices and spreadsheets, in different formats and unlinked to the product on the shelf. The result is regulatory and reputational risk for retailers, marketing statements instead of evidence for regulators, and labels shoppers cannot verify.

## Solution

An evidence engine that traces each product claim through the supplier tiers to its source documents and issues an honest verdict: Verified, Self-declared only, Contradicted or Missing evidence. The result is published in two views. Shoppers see a short, plain-language status with no supplier data. Regulators sign in to a full evidence trail with source quotes, document hashes and a tamper-evident history.

## How Claude is central

Claude does the work that needs reading and language. Code does everything that must be exact and checkable.

- **Claude reads** each supplier document into structured facts, each with an exact quote from the source. Code rejects any fact whose quote is not in the document, whose supplier or tier does not match the registered supply chain, or whose dates or percentages are invalid. Rejected facts are listed with reasons, never dropped silently.
- **Code decides** the verdict with rules per claim type: origin match, certificate validity, recycled-content match, tier coverage, vague wording.
- **Claude explains** the verdict in plain language. Code accepts the explanation only if every cited fact exists, every number, date and country appears in the input, quoted text is real, and the wrong verdict label is not used. Otherwise a template is shown and the fallback is recorded.

The principle: Claude proposes and explains, code verifies, and nothing is shown as evidence that code cannot trace to a source.

## What the demo shows

| Product | Claim | Verdict | Why |
|---|---|---|---|
| Cotton T-shirt | Organic cotton from India | Contradicted | A tier-3 invoice states Bangladesh, and the organic certificate expired before the shipment |
| Bottled water | 100% recycled bottle | Contradicted | Spec and certificate say 60%, and the tier-3 resin supplier has no documents |
| Second bottle | Eco-friendly packaging | Self-declared only | Vague wording that cannot be proven |
| Organic poplin shirt | Organic cotton from India | Verified | India is stated on the declaration and the tier-3 invoice, and a valid organic certificate backs the mill |

Every verdict links to its source quotes. The audit log is SHA-256 hash-chained and stores a hash of every document, and a Verify integrity button recomputes both. The public shopper page is covered by automated tests that check it never exposes supplier names, file names or hashes.

## Impact

- **Retailers:** claims are checked before they are printed, supplier follow-up is specific, and an evidence pack is ready when a regulator asks.
- **Regulators:** claims backed by traceable, tamper-evident evidence instead of statements.
- **Shoppers:** transparency that includes what is not proven, which builds more trust than a blanket green label.
- **Suppliers:** one specific request for the exact document needed, instead of generic questionnaires.

## Feasibility

The prototype runs on a small stack (Python, FastAPI, single-file HTML pages, JSON storage) with no external services except the Claude API. 301 automated tests pass without a key or network access. The production path is a database, a real identity provider, object storage for documents, a queue for ingestion and batch-level evidence. A cost per product is easy to measure, since each claim needs one extraction call per document and one explanation call.

## Honest limits

Synthetic data and clean text documents only. Demo logins are not real security. The audit log is tamper-evident, not tamper-proof: it does not detect truncation or a full rewrite without an external anchor. Forged certificates are not detected, and carbon calculation is designed but not built. "Verified" means a certificate ID is present and nothing contradicts the claim, not that the certifier is independent. Claim scope is not checked, for example a cotton and linen blend.

## Roadmap

Live document upload, a Claude cross-check that flags findings no rule covers (advisory, validated by code), supplier email drafts, PDF and spreadsheet support, a carbon calculator with labelled emission factors, a human review queue, QR codes on packaging, a claim-scope check, certifier registry checks and an external hash anchor.
