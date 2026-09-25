# Delete integration guide — for the DIKSHA CMS / Strapi custom UI team

**Endpoint:** `DELETE /api/documents/<job_dir>`
**Base URL:** `https://poc-interactivetxtbk.diksha.gov.in`
**Source:** `python_app/app.py` (`delete_document`), `python_app/delete_job.py`
**Audience:** the team building the delete action in the Strapi-hosted custom UI that embeds this editor in an iframe.
**Date:** 2026-09-24

> This document is self-contained — you shouldn't need anything else from this
> repo to integrate. If you want the full picture of every endpoint this app
> exposes, that's `docs/02-API-DOCUMENTATION.md`; this route is also documented
> there as section 7a, alongside all the others.

---

## What this endpoint does

One call, and it's final: it deletes **everything** connected to a converted
document —

- the job record (status, title, page/image counts, the `ref_id` you may have
  used to find it)
- the converted HTML file the editor produces
- every asset the document references — extracted figures, tables, page
  images, and anything uploaded through the editor (`media/`)
- the preserved original PDF the editor's split view uses
- if the document was published, the same set of files in OCI Object Storage
  (the HTML, media, and video buckets)

**There is no undo, no trash, no soft delete.** If a creator clicks delete in
your UI, this call should only fire after your UI has confirmed that's really
what they want.

This is a different thing from the automatic retention cleanup this app also
runs: that job only ever removes *unpublished* content that's aged past a
TTL, and it explicitly refuses to touch anything that's been published. This
endpoint has no such restriction — it deletes on request, published or not.

---

## Before you integrate: how you got the `job_dir`

You already have `job_dir` from earlier in the flow — it's the same value
that appears in:

- The `editUrl` / `renderUrl` you got back from `/api/lookup/<refId>` (it's
  the path segment right before the filename: `/output/<job_dir>/<file>.html`)
- The URL of the editor iframe itself, if you're pointing it at
  `.../output/<job_dir>/<file>.html` directly

It looks like `a1b2c3d4_Chapter 5 — 79-99` — an 8-character id, an underscore,
then the original filename's stem. **Treat it as an opaque string.** It can
contain spaces and punctuation (including em-dashes) from the original PDF's
filename, so:

- URL-encode it when building the request path.
- Don't try to parse or reconstruct it — just pass through whatever you
  already have.

---

## Making the call

```
DELETE https://poc-interactivetxtbk.diksha.gov.in/api/documents/{job_dir}
X-Delete-Token: <shared secret — ask the conversion-tool team for this>
```

No request body. `job_dir` is a path segment, URL-encoded.

### Example (curl)

```bash
curl -X DELETE \
  "https://poc-interactivetxtbk.diksha.gov.in/api/documents/a1b2c3d4_Chapter%205%20%E2%80%94%2079-99" \
  -H "X-Delete-Token: <the shared secret>"
```

### Example (Node / fetch, as you'd call it from Strapi)

```js
async function deleteConvertedDocument(jobDir, deleteToken) {
  const url = `https://poc-interactivetxtbk.diksha.gov.in/api/documents/${encodeURIComponent(jobDir)}`;
  const res = await fetch(url, {
    method: "DELETE",
    headers: { "X-Delete-Token": deleteToken },
  });
  const body = await res.json();
  if (!res.ok) {
    throw new Error(body.error || `Delete failed (HTTP ${res.status})`);
  }
  return body; // see "Response shape" below
}
```

---

## Authentication — two layers, both required

This call has to clear two separate checks, and you need credentials for
both from the conversion-tool team; neither is something you configure on
your own:

1. **Network / gateway.** The route is registered at the OCI API Gateway with
   a `"service"` authorization scope — the same scope your existing
   `/api/lookup/<refId>` integration already uses. If your `/api/lookup`
   calls work today, you have whatever credential (service token / mTLS)
   satisfies this already; this route is configured the same way.
2. **Application-level shared secret.** Because this call is irreversible,
   the app itself also checks an `X-Delete-Token` header against a secret
   value it's configured with. **Get this value from the conversion-tool
   team** — it is not something Strapi generates or negotiates, and it's
   different from any Strapi API token you already hold. Send it on every
   call, exactly as given (it's compared byte-for-byte, case-sensitive).

If the tool team tells you no token has been configured for your
environment yet, the header check is skipped — but don't build against that
as a long-term assumption; ask them to confirm one is set before you rely on
this in production.

---

## Response shape

### Success — `200 OK`

```json
{
  "success": true,
  "job_dir": "a1b2c3d4_Chapter 5",
  "job_id": "a1b2c3d4",
  "output_dir_removed": true,
  "uploads_removed": 0,
  "oci": {
    "attempted": true,
    "buckets": {
      "html":  { "deleted": 1,  "failed": [] },
      "media": { "deleted": 46, "failed": [] },
      "video": { "deleted": 0,  "failed": [] }
    }
  },
  "job_record_removed": true
}
```

`success: true` here means "the delete request was processed," not
necessarily "every single artifact existed and was removed" — a document
that was never published will correctly show `oci.attempted: false` (nothing
to remove there), and that's success, not a partial failure. If you want to
show the creator a confirmation with real numbers, `media.deleted` and
`output_dir_removed` are the two fields worth surfacing; the rest is mostly
useful for your own support/debugging logs.

### Errors

| Status | Body | What it means | What to do |
|---|---|---|---|
| `400` | `{"error": "Invalid job_dir"}` | `job_dir` was empty or looked malformed | Check how you built the URL — this shouldn't happen if you're passing through a `job_dir` you got from this app |
| `401` | `{"error": "Invalid or missing X-Delete-Token"}` | The shared secret is missing or wrong | Check the header is actually being sent, and confirm the value with the tool team — don't retry with the same value |
| `404` | `{"error": "Nothing found for this job_dir.", "job_dir": "..."}` | Already deleted, or never existed | Treat as success for your UI's purposes — the end state ("this document is gone") is what you wanted. Don't retry. |
| `409` | `{"error": "Document is still being converted. Retry once it reaches a terminal state..."}` | The document is mid-conversion right now | Wait and retry — don't delete a document while it's still being built. A reasonable retry is checking `/api/lookup/<refId>` until status is no longer `converting`, then retrying the delete. |

There's no `500` documented here on purpose — every deletion step
(local files, uploads, each OCI bucket, the job record) is independent and
best-effort inside this endpoint. One step failing doesn't take down the
whole request; you'll see it reflected as a smaller number in the relevant
`oci.buckets.*.failed` list rather than an error response. If you see an
empty response body or a connection failure, that's an infrastructure
problem worth escalating rather than something to retry blindly.

---

## Recommended integration pattern

1. Confirm with the creator before calling this at all — your UI is the only
   confirmation step; the API has none.
2. Call `DELETE /api/documents/<job_dir>`.
3. On `200`: remove the document from your UI's list/state. Done.
4. On `404`: also remove it from your UI's list/state — the document is
   already gone, which is the outcome you wanted.
5. On `409`: show the creator "still converting, try again shortly" rather
   than a generic error — this is a normal, expected condition, not a bug.
6. On `401`: don't show this to the creator at all — it's a configuration
   problem on the integration, not something they caused. Log it and alert
   whoever owns the integration.
7. Anything else (network error, unexpected status): retry once with backoff,
   then surface a generic "couldn't delete right now" to the creator.

---

## Things this endpoint deliberately does NOT do

- **It does not ask you to confirm twice, or support a "cancel within N
  seconds" grace period.** If you want that experience, build it in your UI
  before you call this endpoint — once called, it's final on this side.
- **It does not return a job you can poll.** The deletion runs synchronously
  and the response reflects the final state; there's no async job ID here
  the way there is for conversion.
- **It does not remove the document from any listing or index Strapi itself
  maintains.** That's yours to update in step 3 above — this endpoint only
  knows about the conversion tool's own storage.

---

## Questions

Route any integration questions to the conversion tool team
(`poc-interactivetxtbk.diksha.gov.in`) — specifically, you'll need them to:

- Confirm/issue your `X-Delete-Token` value.
- Confirm your existing `/api/lookup` service credential also covers this
  route at the gateway (it should, but worth confirming per environment).
