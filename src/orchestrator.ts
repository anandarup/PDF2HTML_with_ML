import { basename, join, resolve } from "node:path";
import { mkdir } from "node:fs/promises";
import { extractPdfContent } from "./tools/extractPdf.js";
import { buildInteractiveHtml } from "./tools/buildHtml.js";

/**
 * Options for the full PDF-to-HTML conversion pipeline.
 */
export interface ConvertPdfOptions {
  /** Absolute or relative path to the source PDF file. */
  pdfPath: string;
  /** Directory for all output (HTML file + images). Defaults to `./output/<pdfName>/`. */
  outputDir?: string;
  /** Title for the HTML document. Defaults to the PDF filename without extension. */
  title?: string;
  /** Whether to embed images as base64 data URIs. Defaults to true. */
  embedImages?: boolean;
}

/**
 * Complete result of the conversion pipeline.
 */
export interface ConversionResult {
  /** Absolute path to the generated HTML file. */
  htmlPath: string;
  /** Absolute path to the directory containing extracted images. */
  imageDirectory: string;
  /** Number of images extracted from the PDF. */
  imageCount: number;
  /** Number of pages in the source PDF. */
  pageCount: number;
  /** Byte size of the generated HTML file. */
  htmlFileSize: number;
  /** The intermediate Markdown content (useful for debugging or further processing). */
  markdown: string;
}

/**
 * Orchestrates the full PDF → Markdown → HTML conversion pipeline.
 *
 * Steps:
 * 1. Calls extract_pdf_content to parse PDF into Markdown and extract images.
 * 2. Calls build_interactive_html to generate a styled, responsive HTML5 file.
 * 3. Returns the path to the generated HTML and metadata about the conversion.
 *
 * @throws {Error} If the PDF cannot be read, parsed, or the output cannot be written.
 */
export async function convertPdfToHtml(
  options: ConvertPdfOptions
): Promise<ConversionResult> {
  const { pdfPath, outputDir, title, embedImages = true } = options;

  const resolvedPdfPath = resolve(pdfPath);
  const pdfBaseName = basename(resolvedPdfPath, ".pdf");
  const documentTitle = title ?? pdfBaseName;

  // Determine output directory
  const resolvedOutputDir = outputDir
    ? resolve(outputDir)
    : join(resolve("output"), pdfBaseName);

  await mkdir(resolvedOutputDir, { recursive: true });

  // Determine image output directory (subdirectory of output)
  const imageOutputDir = join(resolvedOutputDir, "images");

  console.log(`[pdf2webview] Starting conversion: ${resolvedPdfPath}`);
  console.log(`[pdf2webview] Output directory: ${resolvedOutputDir}`);

  // --- Step 1: Extract PDF content ---
  console.log("[pdf2webview] Step 1/2: Extracting PDF content...");

  const extractionResult = await extractPdfContent({
    pdfPath: resolvedPdfPath,
    outputImageDir: imageOutputDir,
  });

  console.log(
    `[pdf2webview]   - Extracted ${extractionResult.pageCount} pages`
  );
  console.log(
    `[pdf2webview]   - Found ${extractionResult.imagePaths.length} images`
  );
  console.log(
    `[pdf2webview]   - Markdown length: ${extractionResult.markdown.length} chars`
  );

  if (extractionResult.markdown.trim().length === 0) {
    console.warn(
      "[pdf2webview]   ⚠ Warning: No text content extracted. " +
        "The PDF may be scanned/image-only. HTML will contain only images."
    );
  }

  // --- Step 2: Build interactive HTML ---
  console.log("[pdf2webview] Step 2/2: Building interactive HTML...");

  // Use a fallback markdown if extraction produced nothing
  const markdownContent =
    extractionResult.markdown.trim().length > 0
      ? extractionResult.markdown
      : `# ${documentTitle}\n\n*This document appears to be image-based. Text content could not be extracted.*\n`;

  const htmlResult = await buildInteractiveHtml({
    markdown: markdownContent,
    imagePaths: extractionResult.imagePaths,
    outputDir: resolvedOutputDir,
    outputFilename: `${pdfBaseName}.html`,
    title: documentTitle,
    embedImages,
  });

  console.log(`[pdf2webview]   - HTML file: ${htmlResult.htmlPath}`);
  console.log(
    `[pdf2webview]   - File size: ${formatBytes(htmlResult.fileSize)}`
  );
  console.log("[pdf2webview] ✓ Conversion complete.");

  return {
    htmlPath: htmlResult.htmlPath,
    imageDirectory: extractionResult.imageDirectory,
    imageCount: htmlResult.imageCount,
    pageCount: extractionResult.pageCount,
    htmlFileSize: htmlResult.fileSize,
    markdown: extractionResult.markdown,
  };
}

/**
 * Formats bytes into a human-readable string.
 */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
