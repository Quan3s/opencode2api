// Transport helper for opencode_free mode.
// Python passes request headers in O2A_HEADERS_JSON and the raw body on stdin.
// Bun is required because the OpenCode free tier fingerprints the TLS client:
// Python urllib, curl and Node all receive 429 with an otherwise identical request.
const url = process.argv[2];
if (!url) {
  process.stderr.write("usage: bun_http.js <url>\n");
  process.exit(2);
}

const headers = JSON.parse(process.env.O2A_HEADERS_JSON || "{}");
const body = await Bun.stdin.text();

let response;
try {
  response = await fetch(url, { method: "POST", headers, body });
} catch (error) {
  process.stderr.write(String(error && error.stack ? error.stack : error) + "\n");
  process.exit(1);
}

process.stdout.write(`__O2A_STATUS__ ${response.status}\n`);
process.stdout.write(`__O2A_HEADERS__ ${JSON.stringify(Object.fromEntries(response.headers))}\n`);

if (response.body) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    if (value && value.byteLength) process.stdout.write(decoder.decode(value, { stream: true }));
  }
  const tail = decoder.decode();
  if (tail) process.stdout.write(tail);
}
