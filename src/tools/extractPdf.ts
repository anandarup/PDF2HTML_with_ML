import { readFile, writeFile, mkdir } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { getResolvedPDFJS, getDocumentProxy } from "unpdf";
import pdf2md from "@opendocsg/pdf2md";

/**
 * Result returned by the extract_pdf_content tool.
 */
export interface ExtractionResult {
  /** The Markdown representation of the PDF content. */
  markdown: string;
  /** Absolute paths to extracted images, in page order. */
  imagePaths: string[];
  /** Directory where images were saved. */
  imageDirectory: string;
  /** Number of pages in the source PDF. */
  pageCount: number;
  /** Source PDF file path (resolved absolute). */
  sourcePath: string;
}

/**
 * Options for PDF extraction.
 */
export interface ExtractPdfOptions {
  /** Absolute or relative path to the PDF file. */
  pdfPath: string;
  /** Directory to write extracted images into. Defaults to `<pdfDir>/<pdfName>_images/`. */
  outputImageDir?: string;
}

/**
 * Extracts structured Markdown and embedded images from a PDF file.
 *
 * Uses @opendocsg/pdf2md for text-to-Markdown conversion and unpdf's bundled
 * pdfjs-dist for image extraction via the operator list.
 *
 * @throws {Error} If the file does not exist, is not a valid PDF, or extraction fails.
 */
export async function extractPdfContent(
  options: ExtractPdfOptions
): Promise<ExtractionResult> {
  const { pdfPath, outputImageDir } = options;

  const resolvedPath = resolve(pdfPath);
  const pdfBuffer = await readFile(resolvedPath);

  if (pdfBuffer.length === 0) {
    throw new Error(`PDF file is empty: ${resolvedPath}`);
  }

  // Determine image output directory
  const pdfBaseName = basename(resolvedPath, ".pdf");
  const imageDir = outputImageDir
    ? resolve(outputImageDir)
    : join(dirname(resolvedPath), `${pdfBaseName}_images`);

  await mkdir(imageDir, { recursive: true });

  // --- Step 1: Convert PDF to Markdown ---
  const dataBuffer = new Uint8Array(pdfBuffer);
  const markdown = await pdf2md(dataBuffer);

  if (!markdown || markdown.trim().length === 0) {
    console.warn(
      `[extract_pdf_content] Warning: No text content extracted from ${resolvedPath}. ` +
        "The PDF may be scanned/image-only."
    );
  }

  // --- Step 2: Extract images via pdfjs operator list ---
  const imagePaths = await extractImages(dataBuffer, imageDir, pdfBaseName);

  // Get page count
  const pdfDoc = await getDocumentProxy(dataBuffer);
  const pageCount = pdfDoc.numPages;

  return {
    markdown,
    imagePaths,
    imageDirectory: imageDir,
    pageCount,
    sourcePath: resolvedPath,
  };
}

/**
 * Scans PDF operator lists to find embedded images and writes them to disk.
 * Uses the pdfjs-dist version bundled with unpdf to avoid version conflicts.
 */
async function extractImages(
  pdfData: Uint8Array,
  imageDir: string,
  filePrefix: string
): Promise<string[]> {
  const pdfjs = await getResolvedPDFJS();
  const OPS = pdfjs.OPS;
  const doc = await getDocumentProxy(new Uint8Array(pdfData));
  const imagePaths: string[] = [];
  let imageIndex = 0;

  for (let pageNum = 1; pageNum <= doc.numPages; pageNum++) {
    const page = await doc.getPage(pageNum);
    const operatorList = await page.getOperatorList();

    for (let i = 0; i < operatorList.fnArray.length; i++) {
      const fn = operatorList.fnArray[i];

      if (
        fn === OPS.paintImageXObject ||
        fn === OPS.paintInlineImageXObject
      ) {
        const args = operatorList.argsArray[i];
        let imageData: ImagePixelData | null = null;

        if (fn === OPS.paintImageXObject) {
          const objId = args[0] as string;
          try {
            const imgObj = await getPageObject(page, objId);
            imageData = extractImageDataFromObj(imgObj);
          } catch {
            // Image object may not be available; skip gracefully
            continue;
          }
        } else if (fn === OPS.paintInlineImageXObject) {
          const imgObj = args[0] as Record<string, unknown>;
          imageData = extractImageDataFromObj(imgObj);
        }

        if (imageData && imageData.data.length > 0) {
          const filename = `${filePrefix}_page${pageNum}_img${imageIndex}.png`;
          const filePath = join(imageDir, filename);

          const pngBuffer = createMinimalPng(
            imageData.data,
            imageData.width,
            imageData.height
          );
          await writeFile(filePath, pngBuffer);
          imagePaths.push(filePath);
          imageIndex++;
        }
      }
    }

    page.cleanup();
  }

  return imagePaths;
}

/**
 * Gets an object from a PDF page by ID, with a timeout.
 */
function getPageObject(
  page: { objs: { get: (id: string, callback: (obj: unknown) => void) => void } },
  objId: string
): Promise<Record<string, unknown>> {
  return new Promise((resolveObj, rejectObj) => {
    const timeout = setTimeout(() => {
      rejectObj(new Error(`Timeout getting object ${objId}`));
    }, 5000);

    page.objs.get(objId, (obj: unknown) => {
      clearTimeout(timeout);
      if (obj) {
        resolveObj(obj as Record<string, unknown>);
      } else {
        rejectObj(new Error(`Image object ${objId} not found`));
      }
    });
  });
}

interface ImagePixelData {
  data: Uint8Array;
  width: number;
  height: number;
}

/**
 * Extracts raw RGBA pixel data from a pdfjs image object.
 */
function extractImageDataFromObj(
  imgObj: Record<string, unknown>
): ImagePixelData | null {
  const width = imgObj["width"] as number | undefined;
  const height = imgObj["height"] as number | undefined;

  if (!width || !height || width <= 0 || height <= 0) {
    return null;
  }

  const rawData =
    (imgObj["data"] as Uint8Array | undefined) ??
    (imgObj["bitmap"] as Uint8Array | undefined);

  if (!rawData || rawData.length === 0) {
    return null;
  }

  // Ensure we have RGBA data
  const expectedRgba = width * height * 4;
  const expectedRgb = width * height * 3;
  let rgbaData: Uint8Array;

  if (rawData.length === expectedRgba) {
    rgbaData = new Uint8Array(rawData);
  } else if (rawData.length === expectedRgb) {
    // Convert RGB to RGBA
    rgbaData = new Uint8Array(expectedRgba);
    for (let i = 0, j = 0; i < rawData.length; i += 3, j += 4) {
      rgbaData[j] = rawData[i];
      rgbaData[j + 1] = rawData[i + 1];
      rgbaData[j + 2] = rawData[i + 2];
      rgbaData[j + 3] = 255;
    }
  } else {
    // Unknown format, skip
    return null;
  }

  return { data: rgbaData, width, height };
}

/**
 * Creates a minimal uncompressed PNG from raw RGBA pixel data.
 * Uses unfiltered rows with DEFLATE stored blocks (no compression)
 * to avoid needing zlib — keeps the implementation dependency-free.
 */
function createMinimalPng(
  rgba: Uint8Array,
  width: number,
  height: number
): Buffer {
  const signature = Buffer.from([
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
  ]);

  // IHDR chunk
  const ihdr = Buffer.alloc(25);
  ihdr.writeUInt32BE(13, 0);
  ihdr.write("IHDR", 4);
  ihdr.writeUInt32BE(width, 8);
  ihdr.writeUInt32BE(height, 12);
  ihdr.writeUInt8(8, 16);  // bit depth
  ihdr.writeUInt8(6, 17);  // color type: RGBA
  ihdr.writeUInt8(0, 18);  // compression
  ihdr.writeUInt8(0, 19);  // filter
  ihdr.writeUInt8(0, 20);  // interlace
  const ihdrCrc = crc32(ihdr.subarray(4, 21));
  ihdr.writeUInt32BE(ihdrCrc, 21);

  // IDAT: raw image data with filter byte 0 (None) per row
  const rowSize = width * 4 + 1;
  const rawImageData = Buffer.alloc(height * rowSize);
  for (let y = 0; y < height; y++) {
    const rowOffset = y * rowSize;
    rawImageData[rowOffset] = 0; // filter: None
    const srcOffset = y * width * 4;
    for (let x = 0; x < width * 4; x++) {
      rawImageData[rowOffset + 1 + x] = rgba[srcOffset + x];
    }
  }

  // Wrap in zlib stored (uncompressed) format
  const zlibData = createStoredDeflate(rawImageData);

  const idat = Buffer.alloc(12 + zlibData.length);
  idat.writeUInt32BE(zlibData.length, 0);
  idat.write("IDAT", 4);
  zlibData.copy(idat, 8);
  const idatCrc = crc32(idat.subarray(4, 8 + zlibData.length));
  idat.writeUInt32BE(idatCrc, 8 + zlibData.length);

  // IEND chunk
  const iend = Buffer.from([
    0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
  ]);

  return Buffer.concat([signature, ihdr, idat, iend]);
}

/**
 * Wraps raw data in a zlib stored (no compression) stream.
 */
function createStoredDeflate(data: Buffer): Buffer {
  const maxBlockSize = 65535;
  const numBlocks = Math.ceil(data.length / maxBlockSize) || 1;
  const totalSize = 2 + numBlocks * 5 + data.length + 4;
  const result = Buffer.alloc(totalSize);
  let offset = 0;

  // Zlib header
  result[offset++] = 0x78;
  result[offset++] = 0x01;

  // DEFLATE stored blocks
  let remaining = data.length;
  let srcOffset = 0;
  while (remaining > 0) {
    const blockSize = Math.min(remaining, maxBlockSize);
    const isLast = remaining <= maxBlockSize;
    result[offset++] = isLast ? 0x01 : 0x00;
    result.writeUInt16LE(blockSize, offset);
    offset += 2;
    result.writeUInt16LE(blockSize ^ 0xffff, offset);
    offset += 2;
    data.copy(result, offset, srcOffset, srcOffset + blockSize);
    offset += blockSize;
    srcOffset += blockSize;
    remaining -= blockSize;
  }

  // Adler-32 checksum
  const adler = adler32(data);
  result.writeUInt32BE(adler, offset);

  return result.subarray(0, offset + 4);
}

function adler32(data: Buffer): number {
  let a = 1;
  let b = 0;
  for (let i = 0; i < data.length; i++) {
    a = (a + data[i]) % 65521;
    b = (b + a) % 65521;
  }
  return ((b << 16) | a) >>> 0;
}

const CRC_TABLE: number[] = (() => {
  const table: number[] = new Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      if (c & 1) {
        c = 0xedb88320 ^ (c >>> 1);
      } else {
        c = c >>> 1;
      }
    }
    table[n] = c;
  }
  return table;
})();

function crc32(data: Buffer): number {
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    crc = CRC_TABLE[(crc ^ data[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
