#!/usr/bin/env node

/**
 * PDF2WebView - Convert PDFs to interactive, styled HTML.
 *
 * Usage:
 *   npx tsx src/index.ts <path-to-pdf> [output-dir]
 *
 * Examples:
 *   npx tsx src/index.ts ./testFiles/sample.pdf
 *   npx tsx src/index.ts ./testFiles/sample.pdf ./output/custom
 */

import { convertPdfToHtml } from "./orchestrator.js";

async function main(): Promise<void> {
  const args = process.argv.slice(2);

  if (args.length === 0) {
    console.error("Usage: pdf2webview <path-to-pdf> [output-dir]");
    console.error("");
    console.error("Arguments:");
    console.error("  path-to-pdf  Path to the PDF file to convert");
    console.error("  output-dir   (Optional) Directory for output files");
    process.exit(1);
  }

  const pdfPath = args[0];
  const outputDir = args[1] ?? undefined;

  try {
    const result = await convertPdfToHtml({
      pdfPath,
      outputDir,
      embedImages: false,
    });

    console.log("");
    console.log("=== Conversion Summary ===");
    console.log(`  HTML file:    ${result.htmlPath}`);
    console.log(`  Pages:        ${result.pageCount}`);
    console.log(`  Images:       ${result.imageCount}`);
    console.log(`  HTML size:    ${formatBytes(result.htmlFileSize)}`);
    console.log(`  Image dir:    ${result.imageDirectory}`);
    console.log("");
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : String(error);
    console.error(`[pdf2webview] Error: ${message}`);
    process.exit(1);
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

main();
