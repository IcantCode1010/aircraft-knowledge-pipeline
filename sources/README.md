# Source Document Library

This directory is the controlled intake area for documents used by the aircraft knowledge pipeline.

## Rules

- Do not commit source PDFs, extracted images, archives, or other document binaries to Git.
- Keep each document in the folder matching its authority and purpose.
- Do not combine multiple PDFs into one file before processing.
- Preserve the original filename.
- Record aircraft applicability, operator, revision, effective date, authority, and checksum in the source registry.
- Treat operator documents and licensed manuals as private unless publication rights are explicitly confirmed.
- A document may support several topics, but it receives one stable registry identity.

## Structure

```text
sources/
  incoming/             Unclassified files waiting for registration
  documents/
    qrh/                Quick Reference Handbooks
    mel/                Minimum Equipment Lists
    amm/                Aircraft Maintenance Manuals
    fim/                Fault Isolation Manuals
    fcom/               Flight Crew Operations Manuals
    training/           Approved training and system-reference material
    visual-reference/   Source documents used primarily for reviewed figures
    other/              Sources that do not fit an established authority type
  registry/             Tracked document metadata; no source binaries
```

## Intake workflow

1. Place a new source file in `incoming/`.
2. Identify its document type, aircraft applicability, operator, revision, and authority.
3. Create a metadata record in `registry/`.
4. Calculate and record its checksum.
5. Move the local file into the matching `documents/` folder.
6. Run extraction against the registered document.

The source file remains local or in controlled private storage. Only its metadata, citations, research packets, and approved OKF output belong in Git.

## Suggested filename pattern

Keep the original filename when it is meaningful. If a controlled filename is required, use:

```text
<aircraft>_<document-type>_<operator-or-authority>_<revision>_<effective-date>.<extension>
```

Example:

```text
b737ng_mel_example-operator_rev42_2026-04-01.pdf
```

The filename is not the document identity. Registry records will provide stable IDs even when a source file is superseded.

