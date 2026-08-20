import { writeFile, readFile } from "node:fs/promises";
import { basename, join, relative, resolve } from "node:path";
import { marked } from "marked";

/**
 * Options for building the interactive HTML file.
 */
export interface BuildHtmlOptions {
  /** The Markdown content to convert. */
  markdown: string;
  /** Absolute paths to images extracted from the PDF. */
  imagePaths: string[];
  /** Directory where the HTML file will be written. */
  outputDir: string;
  /** Filename for the output HTML (without path). Defaults to "output.html". */
  outputFilename?: string;
  /** Title for the HTML document. Defaults to "Converted Document". */
  title?: string;
  /** Whether to embed images as base64 data URIs (true) or use relative paths (false). Defaults to true. */
  embedImages?: boolean;
}

/**
 * Result of the build_interactive_html tool.
 */
export interface BuildHtmlResult {
  /** Absolute path to the generated HTML file. */
  htmlPath: string;
  /** Number of images embedded/referenced. */
  imageCount: number;
  /** Byte size of the generated HTML file. */
  fileSize: number;
}

/**
 * Converts Markdown content and associated images into a responsive, styled HTML5 document.
 *
 * The generated HTML includes:
 * - Responsive typography and layout
 * - Dark mode support via prefers-color-scheme
 * - Table of contents generated from headings
 * - Smooth scrolling and print styles
 * - Embedded or referenced images
 * - Code syntax highlighting styles
 *
 * @throws {Error} If Markdown is empty or output directory is invalid.
 */
export async function buildInteractiveHtml(
  options: BuildHtmlOptions
): Promise<BuildHtmlResult> {
  const {
    markdown,
    imagePaths,
    outputDir,
    outputFilename = "output.html",
    title = "Converted Document",
    embedImages = true,
  } = options;

  if (!markdown || markdown.trim().length === 0) {
    throw new Error("Cannot build HTML from empty Markdown content.");
  }

  const resolvedOutputDir = resolve(outputDir);
  const outputPath = join(resolvedOutputDir, outputFilename);

  // Convert Markdown to HTML body
  const htmlBody = await marked.parse(markdown, {
    gfm: true,
    breaks: false,
  });

  // Process images: either embed as data URIs or set up relative paths
  let processedBody = htmlBody;
  if (imagePaths.length > 0) {
    processedBody = await injectImages(
      htmlBody,
      imagePaths,
      resolvedOutputDir,
      embedImages
    );
  }

  // Extract headings for table of contents
  const toc = extractTableOfContents(htmlBody);

  // Build complete HTML document
  const fullHtml = buildFullDocument(title, processedBody, toc);

  await writeFile(outputPath, fullHtml, "utf-8");

  const fileSize = Buffer.byteLength(fullHtml, "utf-8");

  return {
    htmlPath: outputPath,
    imageCount: imagePaths.length,
    fileSize,
  };
}

/**
 * Injects image references into the HTML body.
 * If images weren't already referenced in the Markdown, appends them at the end.
 */
async function injectImages(
  htmlBody: string,
  imagePaths: string[],
  outputDir: string,
  embed: boolean
): Promise<string> {
  const imageElements: string[] = [];

  for (const imgPath of imagePaths) {
    const resolvedImg = resolve(imgPath);
    let src: string;

    if (embed) {
      try {
        const imgBuffer = await readFile(resolvedImg);
        const base64 = imgBuffer.toString("base64");
        const ext = imgPath.split(".").pop()?.toLowerCase() ?? "png";
        const mimeType = getMimeType(ext);
        src = `data:${mimeType};base64,${base64}`;
      } catch {
        // If embedding fails, fall back to relative path
        src = relative(outputDir, resolvedImg);
      }
    } else {
      src = relative(outputDir, resolvedImg);
    }

    const altText = basename(imgPath, `.${imgPath.split(".").pop()}`);
    imageElements.push(
      `<figure class="extracted-image">` +
        `<img src="${escapeHtml(src)}" alt="${escapeHtml(altText)}" loading="lazy" />` +
        `<figcaption>${escapeHtml(altText)}</figcaption>` +
        `</figure>`
    );
  }

  // Append images in a dedicated section if they aren't already referenced
  if (imageElements.length > 0) {
    const imageSection =
      `<section class="extracted-images" aria-label="Extracted images">\n` +
      `<h2 id="extracted-images">Extracted Images</h2>\n` +
      imageElements.join("\n") +
      `\n</section>`;

    return htmlBody + "\n" + imageSection;
  }

  return htmlBody;
}

interface TocEntry {
  level: number;
  text: string;
  id: string;
}

/**
 * Extracts heading elements to build a table of contents.
 */
function extractTableOfContents(html: string): TocEntry[] {
  const headingRegex = /<h([1-3])[^>]*(?:id="([^"]*)")?[^>]*>(.*?)<\/h\1>/gi;
  const entries: TocEntry[] = [];
  let match: RegExpExecArray | null;

  while ((match = headingRegex.exec(html)) !== null) {
    const level = parseInt(match[1], 10);
    const rawText = match[3].replace(/<[^>]+>/g, ""); // Strip inner HTML tags
    const id =
      match[2] || rawText.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

    entries.push({ level, text: rawText, id });
  }

  return entries;
}

/**
 * Builds the complete HTML5 document with embedded styles.
 */
function buildFullDocument(
  title: string,
  body: string,
  toc: TocEntry[]
): string {
  const tocHtml = buildTocHtml(toc);

  // Add IDs to headings in the body for TOC linking
  let processedBody = body;
  for (const entry of toc) {
    // Match the specific heading by its text content to avoid duplicate ID assignment
    const escapedText = entry.text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const headingPattern = new RegExp(
      `(<h${entry.level})([^>]*>)(\\s*${escapedText})`,
      "i"
    );
    // Only add id if this specific heading doesn't already have one
    const match = processedBody.match(headingPattern);
    if (match && !match[2].includes("id=")) {
      processedBody = processedBody.replace(
        headingPattern,
        `$1 id="${entry.id}"$2$3`
      );
    }
  }

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${escapeHtml(title)}</title>
  <style>
${CSS_STYLES}
  </style>
</head>
<body>
  <div class="document-wrapper">
    <aside class="toc-sidebar" aria-label="Table of contents">
      <nav>
        <h2 class="toc-title">Contents</h2>
        ${tocHtml}
      </nav>
    </aside>
    <main class="content" role="main">
      <header class="document-header">
        <h1 class="document-title">${escapeHtml(title)}</h1>
      </header>
      <article class="document-body">
        ${processedBody}
      </article>
    </main>
  </div>
  <button class="back-to-top" aria-label="Back to top" onclick="window.scrollTo({top:0,behavior:'smooth'})">
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M10 16V4M4 10l6-6 6 6"/>
    </svg>
  </button>
  <script>
${JS_SCRIPTS}
  </script>
</body>
</html>`;
}

function buildTocHtml(toc: TocEntry[]): string {
  if (toc.length === 0) {
    return "<p class=\"toc-empty\">No headings found</p>";
  }

  let html = '<ul class="toc-list">';
  for (const entry of toc) {
    const indent = entry.level - 1;
    html += `<li class="toc-item toc-level-${entry.level}" style="padding-left:${indent * 12}px">`;
    html += `<a href="#${escapeHtml(entry.id)}">${escapeHtml(entry.text)}</a>`;
    html += `</li>`;
  }
  html += "</ul>";
  return html;
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function getMimeType(ext: string): string {
  const mimeMap: Record<string, string> = {
    png: "image/png",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    gif: "image/gif",
    webp: "image/webp",
    svg: "image/svg+xml",
    bmp: "image/bmp",
  };
  return mimeMap[ext] ?? "image/png";
}

const CSS_STYLES = `
    :root {
      --color-bg: #ffffff;
      --color-text: #1a1a2e;
      --color-text-secondary: #4a4a6a;
      --color-heading: #16213e;
      --color-link: #0f4c75;
      --color-link-hover: #3282b8;
      --color-border: #e0e0e0;
      --color-code-bg: #f5f5f7;
      --color-blockquote-bg: #f8f9fa;
      --color-blockquote-border: #3282b8;
      --color-toc-bg: #fafbfc;
      --color-toc-active: #0f4c75;
      --font-body: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      --font-heading: "Georgia", "Times New Roman", serif;
      --font-mono: "SF Mono", "Fira Code", "Fira Mono", Menlo, Consolas, monospace;
      --max-width: 780px;
      --sidebar-width: 260px;
    }

    @media (prefers-color-scheme: dark) {
      :root {
        --color-bg: #1a1a2e;
        --color-text: #e0e0e0;
        --color-text-secondary: #a0a0b0;
        --color-heading: #bbe1fa;
        --color-link: #3282b8;
        --color-link-hover: #bbe1fa;
        --color-border: #2a2a4a;
        --color-code-bg: #16213e;
        --color-blockquote-bg: #16213e;
        --color-blockquote-border: #3282b8;
        --color-toc-bg: #16213e;
        --color-toc-active: #bbe1fa;
      }
    }

    *, *::before, *::after {
      box-sizing: border-box;
    }

    html {
      scroll-behavior: smooth;
      font-size: 16px;
    }

    body {
      margin: 0;
      padding: 0;
      font-family: var(--font-body);
      color: var(--color-text);
      background-color: var(--color-bg);
      line-height: 1.7;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }

    .document-wrapper {
      display: flex;
      min-height: 100vh;
    }

    /* Table of Contents Sidebar */
    .toc-sidebar {
      position: sticky;
      top: 0;
      height: 100vh;
      width: var(--sidebar-width);
      min-width: var(--sidebar-width);
      overflow-y: auto;
      padding: 2rem 1rem;
      background-color: var(--color-toc-bg);
      border-right: 1px solid var(--color-border);
      font-size: 0.85rem;
    }

    .toc-title {
      font-family: var(--font-heading);
      font-size: 1rem;
      color: var(--color-heading);
      margin: 0 0 1rem 0;
      padding-bottom: 0.5rem;
      border-bottom: 2px solid var(--color-border);
    }

    .toc-list {
      list-style: none;
      padding: 0;
      margin: 0;
    }

    .toc-item {
      margin: 0.25rem 0;
    }

    .toc-item a {
      display: block;
      padding: 0.3rem 0.5rem;
      color: var(--color-text-secondary);
      text-decoration: none;
      border-radius: 4px;
      transition: color 0.2s, background-color 0.2s;
    }

    .toc-item a:hover,
    .toc-item a:focus {
      color: var(--color-toc-active);
      background-color: var(--color-border);
      outline: none;
    }

    .toc-empty {
      color: var(--color-text-secondary);
      font-style: italic;
    }

    /* Main Content */
    .content {
      flex: 1;
      max-width: var(--max-width);
      margin: 0 auto;
      padding: 3rem 2rem;
    }

    .document-header {
      margin-bottom: 2.5rem;
      padding-bottom: 1.5rem;
      border-bottom: 3px solid var(--color-border);
    }

    .document-title {
      font-family: var(--font-heading);
      font-size: 2.25rem;
      color: var(--color-heading);
      margin: 0;
      line-height: 1.3;
    }

    /* Typography */
    .document-body h1,
    .document-body h2,
    .document-body h3,
    .document-body h4,
    .document-body h5,
    .document-body h6 {
      font-family: var(--font-heading);
      color: var(--color-heading);
      margin-top: 2em;
      margin-bottom: 0.75em;
      line-height: 1.3;
    }

    .document-body h1 { font-size: 1.875rem; }
    .document-body h2 { font-size: 1.5rem; border-bottom: 1px solid var(--color-border); padding-bottom: 0.3em; }
    .document-body h3 { font-size: 1.25rem; }
    .document-body h4 { font-size: 1.1rem; }

    .document-body p {
      margin: 0 0 1.25em;
    }

    .document-body a {
      color: var(--color-link);
      text-decoration: underline;
      text-underline-offset: 2px;
      transition: color 0.2s;
    }

    .document-body a:hover,
    .document-body a:focus {
      color: var(--color-link-hover);
    }

    /* Lists */
    .document-body ul,
    .document-body ol {
      margin: 0 0 1.25em;
      padding-left: 1.5em;
    }

    .document-body li {
      margin-bottom: 0.4em;
    }

    .document-body li > ul,
    .document-body li > ol {
      margin-top: 0.4em;
      margin-bottom: 0;
    }

    /* Code */
    .document-body code {
      font-family: var(--font-mono);
      font-size: 0.875em;
      background-color: var(--color-code-bg);
      padding: 0.15em 0.4em;
      border-radius: 3px;
    }

    .document-body pre {
      background-color: var(--color-code-bg);
      padding: 1.25rem;
      border-radius: 6px;
      overflow-x: auto;
      margin: 0 0 1.5em;
      border: 1px solid var(--color-border);
    }

    .document-body pre code {
      background: none;
      padding: 0;
      font-size: 0.85rem;
      line-height: 1.5;
    }

    /* Blockquotes */
    .document-body blockquote {
      margin: 0 0 1.5em;
      padding: 1rem 1.5rem;
      background-color: var(--color-blockquote-bg);
      border-left: 4px solid var(--color-blockquote-border);
      border-radius: 0 4px 4px 0;
      color: var(--color-text-secondary);
    }

    .document-body blockquote p:last-child {
      margin-bottom: 0;
    }

    /* Tables */
    .document-body table {
      width: 100%;
      border-collapse: collapse;
      margin: 0 0 1.5em;
      font-size: 0.9rem;
    }

    .document-body th,
    .document-body td {
      padding: 0.75rem 1rem;
      text-align: left;
      border: 1px solid var(--color-border);
    }

    .document-body th {
      background-color: var(--color-code-bg);
      font-weight: 600;
      color: var(--color-heading);
    }

    .document-body tr:nth-child(even) {
      background-color: var(--color-blockquote-bg);
    }

    /* Images */
    .document-body img {
      max-width: 100%;
      height: auto;
      border-radius: 4px;
      margin: 1em 0;
    }

    .extracted-images {
      margin-top: 3rem;
      padding-top: 2rem;
      border-top: 2px solid var(--color-border);
    }

    .extracted-image {
      margin: 1.5rem 0;
      text-align: center;
    }

    .extracted-image img {
      max-width: 100%;
      height: auto;
      border: 1px solid var(--color-border);
      border-radius: 6px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
    }

    .extracted-image figcaption {
      margin-top: 0.5rem;
      font-size: 0.85rem;
      color: var(--color-text-secondary);
      font-style: italic;
    }

    /* Horizontal Rule */
    .document-body hr {
      border: none;
      border-top: 2px solid var(--color-border);
      margin: 2.5em 0;
    }

    /* Back to Top Button */
    .back-to-top {
      position: fixed;
      bottom: 2rem;
      right: 2rem;
      width: 44px;
      height: 44px;
      border-radius: 50%;
      border: 1px solid var(--color-border);
      background-color: var(--color-bg);
      color: var(--color-text);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      opacity: 0;
      visibility: hidden;
      transition: opacity 0.3s, visibility 0.3s, background-color 0.2s;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
    }

    .back-to-top.visible {
      opacity: 1;
      visibility: visible;
    }

    .back-to-top:hover,
    .back-to-top:focus {
      background-color: var(--color-code-bg);
      outline: 2px solid var(--color-link);
      outline-offset: 2px;
    }

    /* Responsive Design */
    @media (max-width: 900px) {
      .document-wrapper {
        flex-direction: column;
      }

      .toc-sidebar {
        position: relative;
        height: auto;
        width: 100%;
        min-width: unset;
        border-right: none;
        border-bottom: 1px solid var(--color-border);
        padding: 1.5rem;
      }

      .content {
        padding: 2rem 1.5rem;
      }

      .document-title {
        font-size: 1.75rem;
      }
    }

    @media (max-width: 600px) {
      .content {
        padding: 1.5rem 1rem;
      }

      .document-title {
        font-size: 1.5rem;
      }

      .document-body pre {
        padding: 0.75rem;
        font-size: 0.8rem;
      }

      .toc-sidebar {
        padding: 1rem;
      }
    }

    /* Print Styles */
    @media print {
      .toc-sidebar,
      .back-to-top {
        display: none;
      }

      .document-wrapper {
        display: block;
      }

      .content {
        max-width: 100%;
        padding: 0;
      }

      .document-body a {
        color: inherit;
        text-decoration: underline;
      }

      .document-body a::after {
        content: " (" attr(href) ")";
        font-size: 0.8em;
        color: #666;
      }

      .document-body pre {
        white-space: pre-wrap;
        border: 1px solid #ccc;
      }
    }
`;

const JS_SCRIPTS = `
    // Back to top button visibility
    (function() {
      var btn = document.querySelector('.back-to-top');
      if (!btn) return;

      window.addEventListener('scroll', function() {
        if (window.scrollY > 300) {
          btn.classList.add('visible');
        } else {
          btn.classList.remove('visible');
        }
      }, { passive: true });
    })();

    // Active TOC highlighting
    (function() {
      var tocLinks = document.querySelectorAll('.toc-item a');
      if (tocLinks.length === 0) return;

      var headings = [];
      tocLinks.forEach(function(link) {
        var id = link.getAttribute('href');
        if (id && id.startsWith('#')) {
          var el = document.getElementById(id.substring(1));
          if (el) headings.push({ el: el, link: link });
        }
      });

      function updateActive() {
        var scrollPos = window.scrollY + 100;
        var active = null;

        for (var i = headings.length - 1; i >= 0; i--) {
          if (headings[i].el.offsetTop <= scrollPos) {
            active = headings[i];
            break;
          }
        }

        tocLinks.forEach(function(link) {
          link.style.color = '';
          link.style.fontWeight = '';
        });

        if (active) {
          active.link.style.color = 'var(--color-toc-active)';
          active.link.style.fontWeight = '600';
        }
      }

      window.addEventListener('scroll', updateActive, { passive: true });
      updateActive();
    })();
`;
