"""
Job queue abstraction (decouple conversion from the request path).

Phase 4 of the scalable-deployment plan introduces a `JobQueue` seam so the API
tier can enqueue a conversion and return immediately, while a separate worker
tier consumes and runs the heavy Docling/OCR pipeline.

The default `ThreadQueue` reproduces the current behavior exactly: `enqueue`
runs the handler in a daemon thread in-process (no separate worker needed). The
`OciQueue` (Phase 4 scaled deployment) publishes a message to OCI Queue; a
standalone `worker.py` consumes with visibility-timeout redelivery and
dead-lettering. Both are selected by `config.QUEUE_BACKEND` (thread | queue).

Message shape (dict): {job_id, upload_ref, ref_id, pdf_stem, attempt}
"""

from __future__ import annotations

import abc
import json
import threading
from typing import Callable, Optional


class JobQueue(abc.ABC):
    @abc.abstractmethod
    def enqueue(self, message: dict) -> None:
        """Submit a conversion job for processing."""

    def consume(self, handler: Callable[[dict], None]) -> None:
        """Consume messages, invoking handler(message) for each.

        Only meaningful for out-of-process backends (OciQueue). The ThreadQueue
        runs the handler at enqueue time, so its consume() is a no-op.
        """
        raise NotImplementedError


class ThreadQueue(JobQueue):
    """
    In-process queue = today's behavior.

    `enqueue` spawns a daemon thread that runs the registered handler with the
    message. This preserves the exact single-VM semantics (conversion runs in a
    background thread of the web process) while presenting the same interface as
    the real queue, so `/convert` code is identical across backends.
    """

    def __init__(self, handler: Callable[[dict], None]):
        self._handler = handler

    def enqueue(self, message: dict) -> None:
        threading.Thread(
            target=self._run, args=(message,), daemon=True
        ).start()

    def _run(self, message: dict) -> None:
        try:
            self._handler(message)
        except Exception:
            # The handler is expected to record failure in the job store; never
            # let a thread crash take anything else down.
            pass

    def consume(self, handler: Callable[[dict], None]) -> None:
        # Nothing to consume — work already ran at enqueue time.
        return


class OciQueue(JobQueue):
    """
    OCI Queue backend for the scaled deployment.

    enqueue -> put a JSON message on the queue.
    consume -> long-poll for messages; for each, run handler; on success delete
               (ack); on failure let the visibility timeout redeliver, and after
               max attempts move to the dead-letter handling (mark job error).

    Requires the OCI SDK and a configured queue (QUEUE_OCID / QUEUE_ENDPOINT).
    Implemented against oci.queue.QueueClient; kept import-lazy so the thread
    default never needs it.
    """

    def __init__(self, config_module):
        self._cfg = config_module
        self._client = None
        self._queue_id = getattr(config_module, "QUEUE_OCID", "")
        self._endpoint = getattr(config_module, "QUEUE_ENDPOINT", "")
        self._max_attempts = getattr(config_module, "QUEUE_MAX_ATTEMPTS", 3)
        self._visibility = getattr(config_module, "QUEUE_VISIBILITY_TIMEOUT", 900)

    def _get_client(self):
        if self._client is None:
            import oci
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            # QueueClient needs the data-plane endpoint for the specific queue.
            self._client = oci.queue.QueueClient(
                {}, signer=signer, service_endpoint=self._endpoint or None
            )
        return self._client

    def enqueue(self, message: dict) -> None:
        import oci
        client = self._get_client()
        client.put_messages(
            self._queue_id,
            oci.queue.models.PutMessagesDetails(
                messages=[oci.queue.models.PutMessagesDetailsEntry(
                    content=json.dumps(message)
                )]
            ),
        )

    def consume(self, handler: Callable[[dict], None]) -> None:
        import oci
        client = self._get_client()
        while True:
            resp = client.get_messages(
                self._queue_id,
                visibility_in_seconds=self._visibility,
                timeout_in_seconds=20,   # long poll
                limit=1,
            )
            messages = getattr(resp.data, "messages", None) or []
            for m in messages:
                try:
                    payload = json.loads(m.content)
                except (json.JSONDecodeError, TypeError):
                    # Malformed — remove so it doesn't loop forever.
                    client.delete_message(self._queue_id, m.receipt)
                    continue

                attempt = int(payload.get("attempt", 0)) + 1
                payload["attempt"] = attempt
                try:
                    handler(payload)
                    client.delete_message(self._queue_id, m.receipt)  # ack
                except Exception:
                    if attempt >= self._max_attempts:
                        # Dead-letter: the handler/consumer is responsible for
                        # marking the job "error"; remove the message so it
                        # stops redelivering.
                        try:
                            client.delete_message(self._queue_id, m.receipt)
                        except Exception:
                            pass
                    # else: don't delete -> visibility timeout redelivers it.


def build_job_queue(config_module, handler: Callable[[dict], None]) -> JobQueue:
    """Factory: pick the queue backend from config.QUEUE_BACKEND.

    `handler(message)` is the conversion runner. For ThreadQueue it runs at
    enqueue time (current behavior). For OciQueue the API process only enqueues;
    the standalone worker.py builds an OciQueue and calls consume(handler).
    """
    backend = getattr(config_module, "QUEUE_BACKEND", "thread")
    if backend == "thread":
        return ThreadQueue(handler)
    if backend == "queue":
        return OciQueue(config_module)
    raise ValueError(f"Unknown QUEUE_BACKEND: {backend!r}")
