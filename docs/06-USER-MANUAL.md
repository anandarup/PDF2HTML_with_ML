# PDF2HTML — Complete User Manual (Plain-English Guide)

This is the "if I get stuck, what do I click" guide for the person converting and
enriching a PDF chapter with PDF2HTML — no technical background assumed. Every
screen mentioned here is a real screenshot taken from the running application
(see `docs/manual-images/`), so what you see on your screen should match what's
shown below.

If you're looking for the technical/API reference instead, see
[`02-API-DOCUMENTATION.md`](02-API-DOCUMENTATION.md). This document is the
day-to-day "how do I..." guide.

---

## Contents

1. [What this tool does](#1-what-this-tool-does)
2. [Converting your first PDF](#2-converting-your-first-pdf)
3. [The document screen — what am I looking at?](#3-the-document-screen--what-am-i-looking-at)
4. [Editing a document](#4-editing-a-document)
5. [The formatting toolbar, piece by piece](#5-the-formatting-toolbar-piece-by-piece)
6. [Inserting things: the Insert menu](#6-inserting-things-the-insert-menu)
7. [Adding a video/audio/slide-deck to a section](#7-adding-a-videoaudioslide-deck-to-a-section)
8. [Making a diagram interactive (AI hotspots)](#8-making-a-diagram-interactive-ai-hotspots)
9. [Saving, and what "unsaved changes" means](#9-saving-and-what-unsaved-changes-means)
10. [Publishing your chapter for learners](#10-publishing-your-chapter-for-learners)
11. [What a learner sees when they open the published link](#11-what-a-learner-sees-when-they-open-the-published-link)
12. [Keyboard shortcuts cheat sheet](#12-keyboard-shortcuts-cheat-sheet)
13. [File size limits](#13-file-size-limits)
14. [Troubleshooting — "it's not working"](#14-troubleshooting--its-not-working)
15. [Glossary of terms used in this app](#15-glossary-of-terms-used-in-this-app)

---

## 1. What this tool does

PDF2HTML takes a PDF (usually a textbook chapter) and turns it into a web page
you can edit like a document — add videos, quizzes, flip-cards, highlighted
glossary terms, and more — then publish it as a link learners can open on a
phone, tablet, or computer.

Three things happen, in order, every time:

1. **Convert** — you drop in a PDF, the tool reads its layout (headings,
   paragraphs, tables, images) and turns it into an editable web document.
2. **Enrich** — you use the built-in editor to format text and drop in
   interactive elements.
3. **Publish** — one click uploads everything and gives you a public link.

You never lose the original PDF — it's kept alongside the converted document
so you (or the "Split view" tool, see [§5](#5-the-formatting-toolbar-piece-by-piece))
can always compare the two side by side.

---

## 2. Converting your first PDF

Open the tool's home page in your browser. You'll see this:

![Upload page](manual-images/01-upload-page.png)

1. **Drag your PDF** onto the dashed box, or click **choose a file** to pick one
   from your computer.
2. The file must:
   - End in `.pdf`
   - Be **100 MB or smaller**
   
   If either of those isn't true, you'll see a message right there on the page
   telling you what's wrong (e.g. *"That doesn't look like a PDF"* or the file
   size problem) — nothing gets uploaded.
3. Once the file is accepted, a progress bar appears showing the upload
   percentage, then switches to "Uploading complete — starting conversion…"
   while the server reads the PDF's layout. This step can take anywhere from a
   few seconds (a short, simple PDF) to a couple of minutes (a long or
   scanned/image-based PDF that needs OCR).
4. When it's done, you'll see a success card with the chapter title, page
   count, image count, and an **Open document** button. Click it — that's
   your editable document.

> **Tip:** If the page seems stuck on "Converting…" for a very long time (more
> than ~6 minutes), the tool will tell you itself: *"This is taking longer than
> expected. It may still finish in the background — check back later, or try
> again."* Scanned/photographed PDFs take much longer than text-based PDFs
> because every page has to be read with OCR (optical character recognition).

---

## 3. The document screen — what am I looking at?

After conversion, you land on the document view. This is the **read-only**
view — nothing here is editable yet.

![Document view](manual-images/02-document-view.png)

Key things on this screen:

- **Left sidebar — "Contents"**: a table of contents built automatically from
  the headings in your document. Click any entry to jump to that section.
- **Top-right buttons**:

  ![Top action buttons](manual-images/02b-top-actions.png)

  | Button | What it does |
  |---|---|
  | **Edit** | Switches into edit mode (see [§4](#4-editing-a-document)) |
  | **Generate HTML** (green button) | Publishes the chapter for learners — gives you a shareable link (see [§10](#10-publishing-your-chapter-for-learners)) |

- **Back-to-top button**: a small round arrow button that appears in the
  bottom corner once you scroll down — click it to jump back to the top of the
  page instantly.

Nothing on this screen can be broken by clicking around — **Edit**
and **Generate HTML** both open something you can cancel out of without any
changes being made.

---

## 4. Editing a document

Click the **Edit** button (top right). The page transforms: a new toolbar
slides down from the very top, and the document itself gets a dashed blue
border showing you the editable area.

![Edit mode toolbar](manual-images/03-edit-mode-toolbar.png)

Close-up of that toolbar:

![Edit toolbar close-up](manual-images/03b-edit-toolbar-crop.png)

While you're in this mode:

- Click anywhere in the text and start typing — it behaves like a normal word
  processor (Word, Google Docs).
- Select text to reveal formatting options (bold, color, highlight, etc.) from
  the toolbar.
- Everything you insert (images, videos, quizzes, etc.) comes from the
  **Insert** button in this same toolbar — see [§6](#6-inserting-things-the-insert-menu).

When you're done, you have two choices at the far right of the toolbar:

- **Save** — writes your changes to the document. A small status message
  next to the toolbar (e.g. "Saved") confirms it worked.
- **Cancel** — leaves edit mode. If you made changes since your last save, a
  small dialog box pops up asking:

  ![Discard changes dialog](manual-images/20-confirm-discard-dialog.png)

  Click **OK** to throw away your unsaved changes, or **Cancel** to go back and
  save them first. (This same warning appears if you try to close the browser
  tab with unsaved changes — the browser itself will ask you to confirm.)

> **Tip — Undo/Redo:** While editing, the toolbar's **Undo**/**Redo** arrows
> (or `Ctrl+Z` / `Ctrl+Y`) step backward and forward through your last **5**
> changes. It's a short history on purpose — save often rather than relying on
> a long undo trail.

**Exactly how undo works, in detail:**
- The editor takes a snapshot of the whole document **300 milliseconds after
  you stop typing/editing** (not after every keystroke) — so a burst of fast
  typing counts as one undo step, not dozens.
- Only your **last 5 snapshots** are kept. Making a 6th change permanently
  drops the oldest one — there's no way to go back further than 5 steps, even
  if you haven't saved yet.
- Undo and Redo share that same 5-slot budget: undoing pushes the current
  state onto the Redo list (also capped at 5), so redoing after several undos
  works exactly like you'd expect, just within that 5-step window.
- **The history resets to empty every time you enter Edit mode.** Leaving edit
  mode (Save or Cancel) and coming back in later starts a fresh undo history —
  you can't undo something from a previous editing session.
- This is a custom undo/redo built for this editor — it is *not* the same as
  your browser's native undo, so it only works while the document editor has
  focus and only for changes made through this editor.

---

## 5. The formatting toolbar, piece by piece

Left to right, the edit toolbar is organized into these groups:

### Text style (headings & lists)
Click the dropdown showing "Paragraph" (or "Heading 2", etc.) at the far left:

![Text style dropdown](manual-images/07c-text-style-popover.png)

Choose Paragraph, Heading 1/2/3, a bulleted list, a numbered list, or a quote
block. This changes the whole line/paragraph your cursor is in.

### Bold, Italic, Underline
The **B**, *I*, and <u>U</u> buttons work exactly like every other word
processor — select text first, then click (or press `Ctrl+B` / `Ctrl+I` /
`Ctrl+U`). The "Aa" dropdown next to them holds Strikethrough, Superscript,
and Subscript for less common formatting.

### Text color
Click the **A** with the colored underline to open a palette of 5 preset
colors plus a custom color picker:

![Text color popover](manual-images/07b-text-color-popover.png)

Select some text first, then pick a color. "Remove color" clears it back to
default.

### Highlight (marker & text gradient)
Click the highlighter icon:

![Highlight popover](manual-images/07-highlight-popover.png)

- **Marker** — 5 flat highlight colors (like a real highlighter pen) plus a
  custom color option.
- **Text gradient** — 5 decorative gradient styles that color the *text itself*
  rather than the background, useful for a section title you want to stand
  out.
- **Remove highlight** clears whichever of the two you applied.

### Alignment
Three buttons (left / center / right) — select a paragraph and click one to
align it.

### Insert
Covered in full in [§6](#6-inserting-things-the-insert-menu) below — this is
where every interactive element (images, videos, quizzes, flip-cards, etc.)
comes from.

### Undo / Redo
Two arrow icons. Steps back/forward through your last 5 edits (also
`Ctrl+Z` / `Ctrl+Y`).

### Split view
The icon that looks like a window split down the middle. Turning this on shows
the **original PDF pages** alongside your edited document, so you can check
you haven't missed or misread anything from the source PDF. Click it again to
turn it off.

### Save status
A small text label (e.g. "Saved", "Saving…") that tells you the state of your
last save, right there in the toolbar.

### The "⋯" (more options) menu
Click the three dots near the right edge:

![Overflow menu](manual-images/08-overflow-menu.png)

- **Discard changes** — throws away anything you've typed since the last save
  (asks you to confirm first).
- **Keyboard shortcuts** — opens a quick reference card:

  ![Keyboard shortcuts popover](manual-images/08b-keyboard-shortcuts-popover.png)

---

## 6. Inserting things: the Insert menu

Click **Insert** in the edit toolbar. A searchable panel drops down:

![Insert panel](manual-images/04-insert-panel.png)

Type into the search box to filter (e.g. typing "form" jumps straight to
"Formula"):

![Insert panel search](manual-images/04b-insert-panel-search-formula.png)

Everything you can insert is grouped into four categories: **Media**,
**Interactive**, **Blocks**, **Reference**, and **Math**. Here's what each one
does and what its dialog looks like.

> **After inserting:** almost everything you insert can be **double-clicked**
> later to re-open its settings and change what you typed — you don't have to
> delete and re-insert to make an edit.

### Image
![Insert image dialog](manual-images/06d-image-insert-modal.png)

Paste a direct image URL, **or** click "Choose an image…" to upload a file
from your computer (PNG or JPG, up to **1 MB**). After it's placed in the
document, double-click it to add a caption/description — good practice for
accessibility (screen readers read that description aloud).

### Video
![Insert video dialog](manual-images/06c-video-insert-modal.png)

Paste a YouTube, Vimeo, or direct `.mp4` link, **or** upload your own video
file (up to **1.2 GB**, multiple files at once). Videos are shown with a full
video player once published, and remember where a learner paused if they come
back later.

### H5P activity
![Insert H5P dialog](manual-images/18-h5p-modal.png)

H5P is a format for interactive exercises (quizzes, drag-and-drop, timelines,
etc.) usually built in a separate H5P authoring tool and exported as a `.h5p`
file. Upload that file here (up to **400 MB**), or point to one that's already
hosted somewhere.

### Flip cards
![Insert flip cards dialog](manual-images/12-flipcards-modal.png)

Good for vocabulary or quick-recall questions. Each card has a **front** (the
question/term) and a **back** (the answer/definition) — learners tap a card to
flip it. Click "Add a card" for more, and you can add an image to either face.

### Accordion
![Insert accordion dialog](manual-images/13-accordion-modal.png)

A stack of collapsible panels — good for FAQs or "click to reveal" content.
Each panel needs a title and the content it reveals when clicked.

### Vertical tabs
![Insert vertical tabs dialog](manual-images/14-vtabs-modal.png)

Tab labels run down the left side; clicking one shows its content next to it.
Good for comparing several related topics.

### Horizontal tabs
![Insert horizontal tabs dialog](manual-images/15-htabs-modal.png)

Same idea as vertical tabs, but the labels run across the top in a row.

### Content box
![Insert content box dialog](manual-images/06-content-box-modal.png)

A styled callout box for anything that should visually stand apart from the
regular paragraph text — a definition, a safety warning, an activity
instruction, a "did you know?" aside, etc. You can:
- Pick a **Type** preset (Activity, Experiment, Did you know?, Remember,
  Example, Caution, Think about it, Key term) — this just pre-fills a
  reasonable color/icon combination, you can still change them.
- Pick a **Colour** (Info/Success/Warning/Tip/Highlight).
- Pick an **Icon** from the grid, or none.
- Give it an optional **Title**.
- Type the box's content in the editable area.
- Optionally add an image inside the box.

Live preview at the top of the dialog updates as you type.

### Animated heading
![Insert animated heading dialog](manual-images/17-animheading-modal.png)

A heading where part of the text stays fixed and the rest **rotates through a
list of words** with a fade animation — e.g. "We build ___" cycling through
"innovation / community / knowledge". Type the fixed text, then add each
rotating word (each can have its own color).

### Shape divider
![Insert shape divider dialog](manual-images/16-shapedivider-modal.png)

A decorative wave/zigzag/curve band to visually separate two sections. Choose
the shape, a color, a height (Small/Medium/Large), and whether it should
gently animate ("Motion": Off/Gentle/Playful).

### Glossary
![Glossary dialog](manual-images/06b-glossary-modal.png)

This is chapter-wide, not a single insertion — it manages every glossary term
for the whole document, and it's the same dialog whether you're adding your
first term or your fiftieth. There are two ways to get terms into it:
**typing them in one at a time**, or **bulk-uploading a CSV file**. Both end
up in the exact same list, and both get **highlighted the same way** once
saved — the difference is purely about *how you get the terms into the list*,
not how they behave afterward.

#### How glossary highlighting actually works

However a term gets into the list, here's what happens once you click
**Save All**:

1. The tool searches the **entire chapter's text** for that exact word or
   phrase, matching **whole words only** (so "cell" won't match inside
   "cellular") and **case-insensitively** (so "Photosynthesis" in your
   glossary matches "photosynthesis" in the text too).
2. **Every** matching occurrence in the chapter gets wrapped with a dotted
   underline — not just the first one. If a term appears 12 times in the
   chapter, all 12 get the tooltip treatment.
3. It skips text inside headings, code blocks, links, and buttons — so a term
   accidentally matching part of a heading or a button label won't get
   highlighted there.
4. If two glossary terms overlap (e.g. "carbon" and "carbon dioxide"), the
   **longer phrase wins** — "carbon dioxide" gets highlighted as one term
   rather than "carbon" being highlighted inside it.
5. Hovering or tapping a highlighted term shows its definition in a small
   popup, both while you're editing and in the final published view.

#### Manual entry (typing terms in)

- Click **Add term** to get a new blank row, fill in the **Term** and its
  **Definition**, repeat for as many terms as you need.
- Good for adding a handful of terms, or fixing/wording a definition exactly
  the way you want.
- There's no live check while typing that the term actually appears in the
  chapter — you can save a term that doesn't exist in the text; it simply
  won't visually highlight anywhere (harmless, but worth double-checking the
  spelling matches the chapter if a term isn't lighting up as expected).

#### Bulk CSV upload

- Click **Bulk upload CSV** and choose a `.csv` file. Click **Download
  template** first if you want a starter file with the exact column
  format (`term,definition`) and a few example rows already filled in as a
  guide.
- The CSV needs two columns: **term** and **definition**. A header row
  (`term,definition`, or `word,meaning`, or `keyword,description`) is
  automatically detected and skipped if present — you don't need to remove it
  yourself.
- **Unlike manual entry, the CSV import checks each term against your
  chapter's actual text before adding it** — this is the key difference.
  For every row in the file, one of four things happens, and you get a
  summary of exactly what happened after the upload:
  | Outcome | Why | What you see |
  |---|---|---|
  | **Added** | Term is 2+ characters, has a definition, and is found somewhere in the chapter's text | Counted in "✓ Added N terms from CSV" |
  | **Skipped — not found** | Term isn't found anywhere in the chapter's text (often a typo, or a term meant for a different chapter) | Listed under "⚠ Skipped N terms not found in this chapter" |
  | **Skipped — duplicate** | You already have that exact term (case-insensitive) in the list, either typed in manually or from an earlier CSV row | Listed under duplicates |
  | **Skipped — invalid** | The term is under 2 characters, or the definition column is empty | Listed under invalid rows |
- Terms added via CSV land as ordinary rows in the same list — after import,
  you can still edit, delete, or add more terms manually before clicking
  **Save All**. Nothing is final until you save.
- CSV import is **additive**, not a replacement: uploading a CSV never
  deletes or overwrites terms already in the list, it only adds new ones on
  top (skipping exact duplicates, as above).

**When to use which:** manual entry is best for a handful of terms or when
you want full control over exact wording as you go. CSV bulk upload is best
when you (or someone else) has already prepared a list of terms and
definitions elsewhere — e.g. a spreadsheet a subject-matter expert filled in
— since it saves you from retyping everything, and its built-in "not found in
this chapter" check catches typos/mismatches you might otherwise miss with
manual entry.

### Formula (math/chemistry)
![Insert formula dialog](manual-images/05-formula-modal.png)

For equations and chemical formulae. Two tabs — **Mathematics** and
**Chemistry** — hold click-to-insert templates for common expressions
(fractions, exponents, quadratic formula, common reactions, etc.). You can
also type raw LaTeX directly into the text box (e.g. `E = mc^2` or
`\ce{H2SO4}` for chemistry). Choose **Inline** (sits within a line of text) or
**Display** (its own centered line), and the box below shows a live preview
before you insert.

### Symbol
![Symbol picker](manual-images/05b-symbol-picker.png)

A grid of math/science symbols (±, ≈, ∞, Greek letters, arrows, set notation,
°C, etc.) for quick one-click insertion into your text — no LaTeX needed for
these. Click a symbol to insert it immediately; click **Close** when done.

---

## 7. Adding a video/audio/slide-deck to a section

Separate from the Insert menu above, every heading in the document gets its
own small row of icons right underneath it while you're editing:

![Media attachment icon row](manual-images/19-media-attach-modal.png)

These icons let you attach supplementary material **to that specific
section** — a lecture video, an audio clip, a slide deck (PPTX/PDF), an H5P
activity, or a link — without inserting it directly into the running text.
Click an icon to open the "Add Media" dialog, then either upload a file or
paste a URL depending on the type. Once something is attached, its icon
becomes colored/filled — clicking it again (outside edit mode) pops open the
content in an overlay for viewing/listening, rather than opening the upload
dialog again.

| Media type | Typical file types | Size limit |
|---|---|---|
| Video | any video format | 1.2 GB |
| Audio | any audio format | 50 MB |
| Presentation | `.pptx`, `.ppt`, `.pdf`, `.odp` | 30 MB |
| H5P Content | `.h5p`, `.html`, `.zip` | 400 MB |
| Virtual Lab | `.zip` bundle | 300 MB |
| URL Link | any web link | — |

---

## 8. Making a diagram interactive (AI hotspots)

If your PDF contains a labeled diagram (say, a biology cross-section with
printed labels like "Nucleus", "Mitochondria"), you can turn those labels into
clickable hotspots automatically:

1. While editing, hover over the diagram image — a **"Make Interactive"**
   button appears on top of it.
2. Click it. The button changes to "Analyzing…" while the tool scans the
   image for text labels using AI-based text detection.
3. Once done, each detected label becomes a small clickable/tappable region
   right on top of the image.
4. Learners viewing the published chapter can click or tab-key to any hotspot
   to see a short definition, automatically pulled from Wikipedia the first
   time someone hovers over it.

If the scan fails (e.g. the image has no readable labels), the button briefly
shows an error message and then reverts to "Make Interactive" so you can try
again — nothing is broken by trying.

> This works best on diagrams with clear, printed (not handwritten) text
> labels. Photographs of real objects, or diagrams with no text, won't
> produce useful hotspots.

---

## 9. Saving, and what "unsaved changes" means

- Clicking **Save** in the edit toolbar (or pressing `Ctrl+S`) writes your
  current edits to the document. This is a plain save — it does **not**
  publish anything or make the document visible to learners. It only updates
  the version you (and other editors) see when you open this document again.
- If you leave edit mode (**Cancel**) or try to close the browser tab while
  you have changes that haven't been saved yet, you'll be asked to confirm —
  this is your safety net against losing work by accident.
- Saving and Publishing are two separate steps on purpose: you can save your
  progress many times while working on a chapter, and only **Publish** (next
  section) when you're happy for learners to actually see it.

---

## 10. Publishing your chapter for learners

Once you're happy with the content (in the normal, non-editing document
view), click the green **Generate HTML** button.

![Publish dialog — idle state](manual-images/10-publish-modal.png)

1. Read the description, then click **Generate now**.
2. A progress state appears while the tool uploads your document's HTML and
   every image/video/attachment it references to cloud storage.
3. When it finishes, you'll see a **public link** you can copy and share
   (there's a **Copy** button next to it), plus **Open** to view it in a new
   tab right away.
4. If something goes wrong, you'll see a plain-language error and a **Retry**
   button — the two safe-for-editors messages you might see are *"This
   document could not be found on the server"* or *"Couldn't generate the
   HTML for learners. Please try again — the server log has the details."*
   (the second one means something failed on the server side; retrying is
   usually enough, but if it keeps failing, pass this along to your
   administrator).

**You can re-publish as many times as you like.** Every time you click
**Generate now** again after making more edits, it overwrites the *same*
link with the latest version — so the link you already shared with learners
keeps working and just shows your newest content. You never need to send out
a new link after the first time.

---

## 11. What a learner sees when they open the published link

Once published, the link opens a clean **reading view** — no editing tools,
no toolbars. Learners get:

- **Contents sidebar** — same table of contents you saw while editing, so
  they can jump to any section.

  ![Contents/TOC sidebar](manual-images/11-toc-sidebar.png)

- **A reader toolbar** at the top with:
  - Text size controls (bigger/smaller) and a Sans/Serif font choice.
  - Light / Dark / Auto theme switch.
  - A **Notes** toggle — learners can click the pencil/plus icon next to any
    heading to write a personal note for themselves. Notes are saved only in
    that learner's own browser (not sent anywhere), so they persist even
    without internet access, but only on that device/browser.
  - A **Bookmark** button — saves the learner's current reading position so
    they can pick up where they left off next time they open the link. The
    page also auto-saves their position every 10 seconds while they read, and
    automatically scrolls them back to it when they return.
- **Glossary terms** — any word you added via the Glossary dialog (§6) appears
  with a dotted underline; hovering or tapping shows its definition.
- **Back-to-top button** — appears after scrolling, jumps to the top.
- Any **flip cards**, **accordions**, **tabs**, and **media attachments** you
  added all work exactly as previewed while editing — flip cards respond to
  a tap or the Enter/Space key, tabs and accordions expand on click.

None of this learner-facing behavior needs any setup from you — it's all
built in automatically once you publish.

---

## 12. Keyboard shortcuts cheat sheet

These only work while you're in **Edit mode**:

| Shortcut | Action |
|---|---|
| `Ctrl+B` | Bold |
| `Ctrl+I` | Italic |
| `Ctrl+U` | Underline |
| `Ctrl+S` | Save |
| `Ctrl+Z` | Undo |
| `Ctrl+Y` (or `Ctrl+Shift+Z`) | Redo |
| `Esc` | Close whichever menu/popover is open |

(On a Mac, use `Cmd` instead of `Ctrl`.)

---

## 13. File size limits

If an upload is rejected, it's almost always because of one of these limits:

| What you're uploading | Maximum size |
|---|---|
| The source PDF (on the home page) | 100 MB |
| Video | 1.2 GB |
| H5P activity package | 400 MB |
| Virtual Lab bundle | 300 MB |
| Presentation (PPTX/PPT/PDF/ODP) | 30 MB |
| Audio | 50 MB |
| Image (inserted via Insert → Image) | 1 MB |

If your file is over the limit, the tool will tell you plainly — e.g.
*"File too large. Maximum for video is 1200 MB."* There's no way to raise
these limits yourself from within the app; if you regularly hit a limit,
mention it to your administrator.

---

## 14. Troubleshooting — "it's not working"

**"That doesn't look like a PDF" when I try to upload.**
The file doesn't end in `.pdf`. Some PDFs get renamed with a different
extension by accident when downloaded — check the file name.

**Upload seems stuck at 0% or never finishes.**
Check your internet connection. If it was fine a moment ago, try refreshing
the page and uploading again — the file wasn't accepted server-side until the
progress bar reaches 100%.

**"This is taking longer than expected" during conversion.**
This isn't necessarily a failure — it usually means the PDF is a scan (photos
of pages, not real text), which needs OCR (much slower than a text PDF).
Leave the tab open a little longer, or check back later; the conversion may
still finish in the background even if you see this message.

**I clicked Edit and lost some formatting I expected.**
Nothing is deleted by opening the editor — click **Cancel** (without saving)
to leave edit mode with the document exactly as it was before you started
editing this session.

**I can't find a button I inserted earlier — how do I edit it again?**
Double-click most inserted elements (images, content boxes, flip cards, tabs,
accordions, glossary terms) to re-open their original dialog with your
existing content pre-filled. You don't need to delete and re-insert.

**"Couldn't generate the HTML for learners" when publishing.**
This is a server-side failure and the details are intentionally not shown to
you (to avoid dumping a technical error onto a content editor). Click
**Retry** once. If it keeps happening, tell your administrator — they can
check the server logs, which do have the full detail.

**A published link 404s / shows nothing for a learner.**
Make sure you actually clicked **Generate HTML** (Publish) at least once —
Editing and Saving only update your private working copy, they do **not**
create or update the learner-facing link on their own.

**A video/H5P/image I attached doesn't show up for learners after publishing.**
Re-publish (**Generate HTML** again) after adding new media — the publish
step is what actually uploads attached files to the public storage; adding a
file while editing and only clicking **Save** does not upload it publicly by
itself.

**My note/bookmark disappeared.**
Notes and bookmarks are saved in your own browser only (not on the server).
Switching to a different browser, device, or a private/incognito window won't
show your previous notes — and clearing your browser data will remove them
too.

**Someone else's edits disappeared after I saved.**
This tool doesn't currently support two people editing the *same* document at
the *same* time — the last person to click Save wins. Coordinate with
co-editors so you're not working on the same chapter simultaneously.

---

## 15. Glossary of terms used in this app

| Term | Meaning |
|---|---|
| **Job** | One PDF-to-HTML conversion in progress or completed. |
| **Chapter / Document** | The converted, editable web page produced from your PDF. |
| **Publish / Generate HTML** | The step that uploads your document (and its media) to a public link learners can open. |
| **H5P** | A file format/tool for building interactive exercises (quizzes, drag-and-drop, etc.), authored elsewhere and inserted here. |
| **OCR** | Optical Character Recognition — reading text out of a scanned/photographed page image. Used automatically on scanned PDFs. |
| **Hotspot** | A clickable region placed on top of a diagram, added via "Make Interactive". |
| **Glossary term** | A word/phrase you've defined once that gets automatically highlighted everywhere it appears in the chapter. |
| **Bookmark (learner)** | A learner's saved reading position, stored in their own browser. |
| **Note (learner)** | A learner's personal note attached to a heading, stored in their own browser. |
| **Split view** | An editor-only side-by-side comparison between your edited document and the original PDF pages. |
| **refId** | An internal reference ID used when this tool is embedded inside another system (like a CMS iframe) to look up a specific document — not something you normally need to type yourself. |

---

*This manual reflects the application as of the screenshots taken on
2026‑09‑28. If a button has moved or looks different from what's pictured
here, the app has likely been updated since — the underlying steps described
should still apply.*
