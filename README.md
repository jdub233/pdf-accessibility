# PDF Accessibility — a Claude skill

A skill that turns Claude into a PDF accessibility auditor and remediation
assistant, using free, open tooling (`pikepdf`, `PyMuPDF`, `pdfplumber`). It
audits PDFs against the core WCAG 2.1 AA / PDF/UA / Matterhorn Protocol
checkpoints, triages what is worth fixing and how, and — only when asked —
repairs a document's tag tree behind a verification gate that proves every page
still renders pixel-for-pixel identically to the original.

Accessibility tags, reading order, and alt text are what assistive-technology users rely on to read a PDF. For institutions that publish documents, producing and maintaining them is increasingly a legal and compliance expectation (WCAG, ADA, Section 508).

It follows the [Agent Skills](https://agentskills.io) open standard. Because it
runs bundled Python, it works wherever Claude can execute code — Claude Cowork
(in the desktop app) and Claude Code today (see Installing).

License: **GPL-3.0-or-later**.

## The idea

A common approach in accessibility tooling is to read a PDF's extracted
text and offer an opinion. But a PDF's accessibility lives in structures that
text extraction cannot see — the tag tree, reading order, alt text, table
semantics. These attributes must be correlated with the text in order to be effective.

This skill is built on one rule:

> **Deterministic code for detection; LLM judgment only where semantic meaning enters.**

A bundled Python checker establishes the facts — what is tagged, the heading
hierarchy, missing alt text, table header scope, document language, title, and
so on. Claude's judgment is used only where meaning genuinely enters: writing
alt text from a rendered image, classifying recovered headings, assessing
whether reading order makes sense, and explaining findings in plain language to
a non-technical author. Every visual judgment is grounded by rendering the page
(PDF → PNG → look at it). The model is never used to *detect* defects.

## Who it's for

- **Communicators, faculty, and staff** who publish PDFs and need to know
  whether a document is accessible and what it would take to fix it — in plain
  language, not checker jargon.
- **Accessibility teams** triaging a backlog: which documents are worst, which
  are author-fixable, and which need a dedicated project.
- **Developers** doing programmatic remediation of legacy PDFs where the
  original source file is gone.

## What's in the repo

| Path | What it is |
|---|---|
| `SKILL.md` | The skill itself — the operating instructions Claude loads. |
| `references/audit-interpretation.md` | What each checker finding means, the WCAG criterion behind it, and severity ranking. |
| `references/remediation-patterns.md` | The proven repair recipes (language/title injection, heading re-leveling, alt-text injection, bookmarks, link descriptions, from-scratch tagging). |
| `references/combined-tagger-guide.md` | The scaling architecture for whole-document tagging projects. |
| `scripts/pdf_a11y_check.py` | The audit checker. Emits JSON over the Matterhorn/PDF-UA subset. **Always the first step.** |
| `scripts/verify_tags.py` | The mandatory verification gate: proves pixel identity, reads tagged text back through an independent parser, and checks text coverage. |
| `scripts/table_audit.py` | Checks the two Acrobat table rules (row regularity, header heuristic) the verifier is blind to. |
| `scripts/render_figures.py` | Renders figures lacking alt text to PNG for the render-and-look loop. |
| `scripts/tagger_engine.py` | The reusable, document-agnostic tagging engine. |
| `scripts/example_tagger_assessing_rr_rh.py` | A complete, real worked example config that drives the engine (see note below). |
| `evals/evals.json` | Seed evaluation prompts. |

## Requirements

Python 3, with:

```bash
pip install pikepdf pymupdf "pdfplumber>=0.10"
```

(`pdfplumber` ≥ 0.10 is required because the verifier uses it as an independent
second parser.)

## Workflow

1. **Audit** — run `scripts/pdf_a11y_check.py` on every PDF in scope. Never
   offer an opinion on a PDF's accessibility without running the checker first.
2. **Triage** — classify each document. Tier 1: tagged, sound structure,
   isolated defects → author fixes in the source file and re-exports. Tier 2:
   untagged but the source likely exists → re-export with tagging enabled, do
   not hand-remediate. Tier 3: legacy, tagged-but-broken or source lost, content
   valuable → programmatic repair or HTML re-publication.
3. **Report** — every finding as: what's wrong → which WCAG/Matterhorn criterion
   → who is affected and how → the concrete fix in the tool they have, in plain
   language.
4. **Remediate** — only when asked, only on a copy, and only behind the
   verification gate. The discipline: never alter painting operators; re-run the
   checker and report the before/after delta; never claim PDF/UA conformance
   unless every checkpoint genuinely passes; recommend a human screen-reader pass
   as the acceptance test.

See `SKILL.md` for the full methodology.

## Installing

The skill ships its own Python (the checker, verifier, and taggers), so it needs
an environment that can run Python with the libraries above. **Claude Cowork** (in
the desktop app) and **Claude Code** both provide that. A plain web chat or a
LibreChat-based portal (such as a TerrierGPT-style deployment) can load the
skill's instructions but cannot run the tooling without a code-execution
interface, so it is not a usable target yet.

### Claude Cowork (desktop app)

Zip the contents of this skill folder (the directory containing `SKILL.md`) and
upload it via **Customize → Skills → + → Create skill**. Once installed it is
enabled by default and Claude uses it automatically when relevant; with code
execution available, Claude runs the checker and remediation scripts directly.

### Claude Code

Drop this folder into a skills directory so Claude discovers it in place:

- Personal (all your projects): `~/.claude/skills/pdf-accessibility/`
- Project (this repo only): `.claude/skills/pdf-accessibility/`

Claude loads it automatically; you can also invoke it directly with
`/pdf-accessibility`. For one-command installation across a team, this repo can
be packaged as a plugin and added with
`claude plugin marketplace add <owner>/pdf-accessibility-skill` (plugin manifest
planned — see Status).

### Organization-wide (Claude Team / Enterprise admins)

From **Organization settings → Plugins**, with Cowork and Skills enabled, there
are two routes:

- **Manual upload** — package the skill as a plugin ZIP and upload it. Simplest
  for evaluation and one-off distribution.
- **Private marketplace with GitHub sync** — organization GitHub-synced
  marketplaces must point at a **private or internal** repository; public repos
  are not permitted for org marketplaces. This public repository is therefore the
  open-source upstream — to sync it, mirror the packaged plugin into a private
  marketplace repo and reference it by relative path.

See Anthropic's
[Manage plugins for your organization](https://support.claude.com/en/articles/13837433-manage-plugins-for-your-organization)
and the
[Claude Code skills docs](https://docs.claude.com/en/docs/claude-code/skills).

## Built on real documents

- **From-scratch tagging of a 96-page legacy handbook** (untagged 2009
  production): all 96 pages tagged, 2,605 tagged content items verified, 57
  bookmarks generated, every page proven pixel-identical to the original at
  150 dpi, with Acrobat table rules clean.
- **Tag-tree remediation of a tagged-but-flat InDesign report** (25-page
  magazine-style export where every style was rolemapped to `/P`): 0 → 106
  headings recovered, 101 figure alt texts written, 227 link descriptions, 52
  bookmarks; all 25 pages pixel-identical, 4,384 content items verified.
- **Audit-only triage** across documents spanning all three tiers, surfacing the
  key lesson that "Tagged: yes" is worthless as a triage signal on its own.

## What it cannot do

Be aware of these limits:

- It does **not** OCR scanned documents — it detects them and says so.
- It does **not** verify color contrast yet.
- It cannot fix reading order that is wrong at the content-stream level.
- Its checker is a strong **subset** of — not a replacement for — a certified
  validator such as veraPDF or Acrobat's checker when a formal conformance claim
  is required.

Machine verification proves *structure*, not *experience*. A human screen-reader
pass (NVDA, VoiceOver) is the real acceptance test, and residual defects should
always be included in summary reports.

## A note on the worked example

`scripts/example_tagger_assessing_rr_rh.py` is a complete, **real** per-document
tagging configuration — labeled "an example, not a tool." It contains the title,
some headings, and authored figure descriptions from a Boston University reference handbook, kept so the example shows how a real world per-document config drives the reusable engine
(`tagger_engine.py`). Each document gets its own config like this one; the engine
is reused unchanged.

## License

GPL-3.0-or-later. See [`LICENSE`](LICENSE).
