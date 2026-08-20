---
name: project-overview
description: Overview of the PDF2WebView project structure and capabilities
---

# PDF2WebView Project

This is a PDF-to-HTML document conversion application built with Node.js and TypeScript.

## Architecture

- **src/tools/extractPdf.ts** — `extractPdfContent()`: Parses PDF → Markdown + extracts images
- **src/tools/buildHtml.ts** — `buildInteractiveHtml()`: Converts Markdown + images → responsive HTML5
- **src/orchestrator.ts** — `convertPdfToHtml()`: Wires extraction → HTML generation pipeline
- **src/index.ts** — CLI entry point

## Dependencies

- `@opendocsg/pdf2md` — PDF text extraction to Markdown
- `marked` — Markdown to HTML conversion
- `pdfjs-dist` — PDF image extraction via operator lists

## Commands

- `npm run dev` — Run via tsx (development)
- `npm run build` — Compile TypeScript
- `npx tsx src/index.ts <pdf-path> [output-dir]` — Convert a PDF

## Agent

The `pdf-converter` agent (`.kiro/agents/pdf-converter.md`) orchestrates the conversion pipeline when users upload PDFs.
