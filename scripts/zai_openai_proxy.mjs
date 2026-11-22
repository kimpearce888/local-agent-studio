// OpenAI-compatible SSE proxy over the z-ai SDK (reference-model
// endpoint for the local-agent-studio benchmark).
//
// The engine's ChatCompletionsAdapter speaks the OpenAI wire format
// (POST /v1/chat/completions with SSE streaming, GET /v1/models).
// The z-ai SDK is a non-streaming Node SDK, so this proxy:
//   - maps wire messages to SDK messages ('tool' results become user
//     turns with a prefix; native tools are ignored - the capability
//     probe will correctly report tool_calling=false and the engine
//     falls back to the text tool protocol);
//   - translates response_format json_schema into a prompt instruction
//     (prompting, not grammar enforcement - recorded as such);
//   - emits the reply as SSE chunks + a final usage chunk + [DONE].
//
// Run from this directory (node_modules here resolves the SDK):
//   node zai-openai-proxy.mjs   # listens on 127.0.0.1:8466

import http from 'node:http';
import ZAI from 'z-ai-web-dev-sdk';

const PORT = 8466;
const MODEL = 'glm-4-plus';

const zai = await ZAI.create();

// The upstream service rate-limits under load (measured: 102 x HTTP
// 429 during the first reference-benchmark run).  Two defences:
//   - every SDK call is serialized behind a gate with a minimum
//     spacing, so the benchmark's sequential traffic stays polite;
//   - a 429 is retried with exponential backoff (3s, 6s, 12s, 24s).
const MIN_SPACING_MS = 4000;
let chain = Promise.resolve();
let lastCall = 0;

function gated(fn) {
  const run = chain.then(async () => {
    const gap = Date.now() - lastCall;
    if (gap < MIN_SPACING_MS) {
      await new Promise((r) => setTimeout(r, MIN_SPACING_MS - gap));
    }
    try {
      return await fn();
    } finally {
      lastCall = Date.now();
    }
  });
  chain = run.then(() => {}, () => {});
  return run;
}

async function callSdkRetry(body) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await gated(() => callSdk(body));
    } catch (err) {
      const msg = String((err && err.message) || err);
      if (attempt < 5 && (msg.includes('429') || msg.includes('Too many'))) {
        const wait = [5000, 15000, 45000, 120000, 300000][attempt];
        console.error(`[proxy] 429 backoff ${wait}ms (attempt `
          + `${attempt + 1}/5)`);
        await new Promise((r) => setTimeout(r, wait));
        continue;
      }
      throw err;
    }
  }
}

function toSdkMessages(messages) {
  const out = [];
  for (const m of messages || []) {
    if (m.role === 'tool') {
      out.push({
        role: 'user',
        content: `[tool result for ${m.name || m.tool_call_id || 'tool'}]\n${m.content || ''}`,
      });
    } else if (m.role === 'system') {
      out.push({ role: 'system', content: m.content || '' });
    } else if (m.role === 'user' || m.role === 'assistant') {
      out.push({ role: m.role, content: m.content || '' });
    }
    // tool_calls inside assistant messages are dropped: the proxy does
    // not do native tool calling, the engine never sends them on this
    // path (tool_calling=false -> text tool protocol).
  }
  return out;
}

async function callSdk(body) {
  const messages = toSdkMessages(body.messages);
  const rf = body.response_format;
  if (rf && rf.type === 'json_schema' && rf.json_schema && rf.json_schema.schema) {
    const last = messages[messages.length - 1];
    if (last) {
      last.content += '\n\nReply with a single JSON object that conforms to this JSON schema. No prose, no code fences:\n'
        + JSON.stringify(rf.json_schema.schema);
    }
  }
  const params = { messages, thinking: { type: 'disabled' } };
  if (typeof body.temperature === 'number') params.temperature = body.temperature;
  if (typeof body.max_tokens === 'number' && body.max_tokens > 0) {
    params.max_tokens = body.max_tokens;
  }
  const t0 = Date.now();
  const r = await zai.chat.completions.create(params);
  const content = (r.choices && r.choices[0] && r.choices[0].message
                   && r.choices[0].message.content) || '';
  const usage = r.usage || {};
  return { content, usage, ms: Date.now() - t0 };
}

const server = http.createServer(async (req, res) => {
  const url = (req.url || '').split('?')[0];
  if (req.method === 'GET' && (url === '/v1/models' || url === '/models')) {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      object: 'list',
      data: [{ id: MODEL, object: 'model', created: 1700000000, owned_by: 'zai' }],
    }));
    return;
  }
  if (req.method === 'GET' && (url === '/health' || url === '/')) {
    res.writeHead(200);
    res.end('ok');
    return;
  }
  if (req.method === 'POST'
      && (url === '/v1/chat/completions' || url === '/chat/completions')) {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    let body;
    try {
      body = JSON.parse(raw);
    } catch {
      res.writeHead(400);
      res.end('bad json');
      return;
    }
    try {
      const { content, usage, ms } = await callSdkRetry(body);
      const id = 'chatcmpl-' + Date.now();
      if (body.stream) {
        res.writeHead(200, {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          Connection: 'keep-alive',
        });
        const send = (obj) => res.write('data: ' + JSON.stringify(obj) + '\n\n');
        send({ id, object: 'chat.completion.chunk', model: MODEL,
               choices: [{ index: 0, delta: { role: 'assistant' },
                           finish_reason: null }] });
        const PIECE = 512;
        for (let i = 0; i < content.length; i += PIECE) {
          send({ id, object: 'chat.completion.chunk', model: MODEL,
                 choices: [{ index: 0,
                             delta: { content: content.slice(i, i + PIECE) },
                             finish_reason: null }] });
        }
        send({ id, object: 'chat.completion.chunk', model: MODEL,
               choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
               usage: { prompt_tokens: usage.prompt_tokens || 0,
                        completion_tokens: usage.completion_tokens || 0 } });
        res.write('data: [DONE]\n\n');
        res.end();
      } else {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          id, object: 'chat.completion',
          created: Math.floor(Date.now() / 1000), model: MODEL,
          choices: [{ index: 0,
                      message: { role: 'assistant', content },
                      finish_reason: 'stop' }],
          usage: { prompt_tokens: usage.prompt_tokens || 0,
                   completion_tokens: usage.completion_tokens || 0 },
        }));
      }
      console.error(`[proxy] ${new Date().toISOString()} ${ms}ms in=${usage.prompt_tokens || 0} `
        + `out=${usage.completion_tokens || 0} content=${content.length}ch`);
    } catch (err) {
      console.error('[proxy] ERROR', err && err.message);
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        error: { message: String((err && err.message) || err),
                 type: 'proxy_error' } }));
    }
    return;
  }
  res.writeHead(404);
  res.end('not found');
});

server.listen(PORT, '127.0.0.1',
  () => console.error(`[proxy] OpenAI-compatible z-ai proxy on `
    + `http://127.0.0.1:${PORT}/v1 (model ${MODEL})`));
