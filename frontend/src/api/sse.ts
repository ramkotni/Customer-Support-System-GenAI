/**
 * Minimal Server-Sent Events reader for `fetch` response bodies.
 *
 * `EventSource` can't issue a POST or send custom headers, so the stream is
 * read off the response body directly. Frames are `event: <name>` +
 * `data: <json>` separated by a blank line; comment lines (`: ...`) are
 * ignored, which is how the backend flushes headers early.
 */
export interface SseEvent<T = unknown> {
  event: string;
  data: T;
}

export async function* readEventStream<T = unknown>(
  response: Response,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent<T>> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // A frame ends at a blank line. Tolerate CRLF as well as LF.
      let boundary = findBoundary(buffer);
      while (boundary !== -1) {
        const raw = buffer.slice(0, boundary.valueOf());
        buffer = buffer.slice(boundary + boundaryLength(buffer, boundary));
        const parsed = parseFrame<T>(raw);
        if (parsed) yield parsed;
        boundary = findBoundary(buffer);
      }

      // An aborted stream must *reject*, not end cleanly: the caller
      // distinguishes "stopped" from "finished" by the thrown error.
      throwIfAborted(signal);
    }

    throwIfAborted(signal);

    // Flush a trailing frame that arrived without its blank line.
    const tail = parseFrame<T>(buffer);
    if (tail) yield tail;
  } finally {
    // Releasing the lock lets an aborted fetch tear the connection down.
    try {
      reader.releaseLock();
    } catch {
      /* already released */
    }
  }
}

function throwIfAborted(signal?: AbortSignal): void {
  if (!signal?.aborted) return;
  throw new DOMException('The stream was aborted.', 'AbortError');
}

function findBoundary(buffer: string): number {
  const lf = buffer.indexOf('\n\n');
  const crlf = buffer.indexOf('\r\n\r\n');
  if (lf === -1) return crlf;
  if (crlf === -1) return lf;
  return Math.min(lf, crlf);
}

function boundaryLength(buffer: string, index: number): number {
  return buffer.startsWith('\r\n\r\n', index) ? 4 : 2;
}

function parseFrame<T>(raw: string): SseEvent<T> | null {
  let event: string | null = null;
  const dataLines: string[] = [];

  for (const line of raw.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue; // blank or comment
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
  }

  if (event === null) return null;

  let data: unknown = undefined;
  if (dataLines.length) {
    try {
      data = JSON.parse(dataLines.join('\n'));
    } catch {
      data = dataLines.join('\n');
    }
  }
  return { event, data: data as T };
}
