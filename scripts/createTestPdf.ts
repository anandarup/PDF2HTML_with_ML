import { writeFile } from "node:fs/promises";

async function main(): Promise<void> {
  // Create a minimal valid PDF with structured text content
  const pdf = [
    "%PDF-1.4",
    "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj",
    "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj",
    "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj",
    "4 0 obj<</Length 440>>stream",
    "BT",
    "/F1 24 Tf",
    "72 720 Td",
    "(PDF2WebView Test Document) Tj",
    "0 -50 Td",
    "/F1 16 Tf",
    "(Introduction) Tj",
    "0 -25 Td",
    "/F1 12 Tf",
    "(This is a sample PDF document created to validate the PDF-to-HTML) Tj",
    "0 -18 Td",
    "(conversion pipeline. It includes multiple sections and paragraphs.) Tj",
    "0 -35 Td",
    "/F1 16 Tf",
    "(Key Features) Tj",
    "0 -25 Td",
    "/F1 12 Tf",
    "(1. Markdown extraction from PDF text content) Tj",
    "0 -18 Td",
    "(2. Image extraction from embedded graphics) Tj",
    "0 -18 Td",
    "(3. Responsive HTML5 generation with dark mode) Tj",
    "0 -18 Td",
    "(4. Table of contents with scroll tracking) Tj",
    "0 -35 Td",
    "/F1 16 Tf",
    "(Conclusion) Tj",
    "0 -25 Td",
    "/F1 12 Tf",
    "(The pipeline successfully converts structured PDFs into readable web pages.) Tj",
    "ET",
    "endstream",
    "endobj",
    "5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj",
    "xref",
    "0 6",
    "0000000000 65535 f ",
    "0000000009 00000 n ",
    "0000000058 00000 n ",
    "0000000115 00000 n ",
    "0000000266 00000 n ",
    "0000000758 00000 n ",
    "trailer<</Size 6/Root 1 0 R>>",
    "startxref",
    "829",
    "%%EOF",
  ].join("\n");

  await writeFile("./testFiles/sample.pdf", pdf);
  console.log("Test PDF created at ./testFiles/sample.pdf");
  console.log("Size:", Buffer.byteLength(pdf), "bytes");
}

main();
