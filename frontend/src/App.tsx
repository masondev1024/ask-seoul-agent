import { FormEvent, useMemo, useState } from 'react';

const KNOWN_EVENTS = new Set([
  'session.start',
  'assistant.status',
  'tool.call',
  'tool.result',
  'assistant.final',
  'session.error',
  'session.done',
]);

type Evidence = {
  product_id?: string;
  source?: string;
  freshness?: string;
  rows?: unknown[];
  [key: string]: unknown;
};

type AgentPayload = {
  trace_id?: string;
  provider_mode?: string;
  status?: string;
  tool_name?: string;
  input?: unknown;
  output?: unknown;
  evidence?: Evidence[];
  answer?: string;
  envelope?: {
    answer?: string;
    evidence?: Evidence[];
    evidence_status?: string;
    [key: string]: unknown;
  };
  error?: string | { code?: string; message?: string; retryable?: boolean };
  message?: string;
  [key: string]: unknown;
};

type TimelineEvent = {
  id: number;
  name: string;
  data: AgentPayload;
};

type ParsedSseEvent = {
  event: string;
  data: string;
};

function parseSseBlock(block: string): ParsedSseEvent | null {
  let event = 'message';
  const dataLines: string[] = [];

  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim();
      continue;
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).trimStart());
    }
  }

  if (!dataLines.length) {
    return null;
  }
  return { event, data: dataLines.join('\n') };
}

function stringifyBounded(value: unknown): string {
  const text = JSON.stringify(value, null, 2);
  if (!text) {
    return '';
  }
  return text.length > 1600 ? `${text.slice(0, 1600)}\n... truncated` : text;
}

async function readAgentStream(
  response: Response,
  onEvent: (eventName: string, payload: AgentPayload) => void,
): Promise<void> {
  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }
  if (!response.body) {
    throw new Error('Response did not include a stream body.');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split('\n\n');
    buffer = blocks.pop() ?? '';

    for (const block of blocks) {
      const parsed = parseSseBlock(block.trimEnd());
      if (!parsed || !KNOWN_EVENTS.has(parsed.event)) {
        continue;
      }
      let payload: AgentPayload;
      try {
        payload = JSON.parse(parsed.data) as AgentPayload;
      } catch {
        throw new Error(`Invalid stream payload for ${parsed.event}`);
      }
      onEvent(parsed.event, payload);
    }

    if (done) {
      break;
    }
  }

  const tail = parseSseBlock(buffer.trim());
  if (tail && KNOWN_EVENTS.has(tail.event)) {
    try {
      onEvent(tail.event, JSON.parse(tail.data) as AgentPayload);
    } catch {
      throw new Error(`Invalid stream payload for ${tail.event}`);
    }
  }
}

export default function App() {
  const [question, setQuestion] = useState('');
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [answer, setAnswer] = useState('');
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [providerMode, setProviderMode] = useState('not connected');
  const [traceId, setTraceId] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const canSubmit = question.trim().length > 0 && !isLoading;
  const latestStatus = useMemo(() => {
    const statusEvent = [...events]
      .reverse()
      .find((event) => typeof event.data.status === 'string');
    return statusEvent?.data.status ?? (isLoading ? 'Running agent workflow' : 'Idle');
  }, [events, isLoading]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion || isLoading) {
      return;
    }

    setEvents([]);
    setAnswer('');
    setEvidence([]);
    setTraceId('');
    setError('');
    setProviderMode('connecting');
    setIsLoading(true);

    try {
      const response = await fetch('/api/v1/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: trimmedQuestion }),
      });

      let eventId = 0;
      await readAgentStream(response, (eventName, payload) => {
        setEvents((current) => [...current, { id: eventId, name: eventName, data: payload }]);
        eventId += 1;

        if (payload.trace_id) {
          setTraceId(payload.trace_id);
        }
        if (payload.provider_mode) {
          setProviderMode(payload.provider_mode);
        }
        const payloadEvidence = payload.evidence;
        if (eventName === 'tool.result' && Array.isArray(payloadEvidence)) {
          setEvidence((current) => [...current, ...payloadEvidence]);
        }
        if (eventName === 'assistant.final') {
          const envelope = payload.envelope;
          setAnswer(typeof envelope?.answer === 'string' ? envelope.answer : '');
          if (Array.isArray(envelope?.evidence)) {
            setEvidence(envelope.evidence);
          }
        }
        if (eventName === 'session.error') {
          const structuredError = payload.error;
          setError(
            typeof structuredError === 'string'
              ? structuredError
              : structuredError?.message ?? payload.message ?? 'Agent session failed.',
          );
        }
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unexpected UI error.');
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="app-shell">
      <section className="workspace" aria-labelledby="app-title">
        <header className="topbar">
          <div>
            <p className="eyebrow">ASK Seoul Agent Console</p>
            <h1 id="app-title">Grounded city-data assistant</h1>
          </div>
          <div className="status-stack" aria-live="polite">
            <span className="mode-badge">{providerMode === 'demo' ? 'Demo mode' : providerMode}</span>
            {traceId ? <span className="trace-badge">trace {traceId}</span> : null}
          </div>
        </header>

        <div className="demo-warning" role="note">
          Demo responses verify the local workflow and are not LLM output. Preview evidence remains five-row sample-only in every mode.
        </div>

        <form className="question-form" onSubmit={handleSubmit}>
          <label htmlFor="question">Question</label>
          <div className="input-row">
            <textarea
              id="question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="Ask about a Seoul data product, risk window, or available catalog evidence."
              maxLength={500}
              rows={3}
            />
            <button type="submit" disabled={!canSubmit} aria-label="Send">
              {isLoading ? 'Sending' : 'Send'}
            </button>
          </div>
        </form>

        {error ? (
          <div className="error-panel" role="alert">
            {error}
          </div>
        ) : null}

        <section className="content-grid">
          <article className="panel answer-panel" aria-labelledby="answer-heading">
            <div className="panel-header">
              <h2 id="answer-heading">Answer</h2>
              <span>{latestStatus}</span>
            </div>
            {isLoading && !answer ? <p className="muted">Waiting for streamed agent output...</p> : null}
            {!isLoading && !answer && !error ? <p className="muted">No answer yet.</p> : null}
            {answer ? <p className="answer-text">{answer}</p> : null}
          </article>

          <article className="panel timeline-panel" aria-labelledby="timeline-heading">
            <div className="panel-header">
              <h2 id="timeline-heading">Agent timeline</h2>
              <span>{events.length} events</span>
            </div>
            {events.length === 0 ? (
              <p className="muted">Submit a question to see session, tool, and final-answer events.</p>
            ) : (
              <ol className="timeline">
                {events.map((event) => (
                  <li key={`${event.name}-${event.id}`}>
                    <strong>{event.name}</strong>
                    <span>{event.data.tool_name ?? event.data.status ?? event.data.trace_id ?? 'received'}</span>
                  </li>
                ))}
              </ol>
            )}
          </article>
        </section>

        <section className="panel evidence-panel" aria-labelledby="evidence-heading">
          <div className="panel-header">
            <h2 id="evidence-heading">Evidence</h2>
            <span>{evidence.length} cards</span>
          </div>
          {evidence.length === 0 ? (
            <p className="muted">Evidence cards will show product, source, freshness, and bounded raw rows.</p>
          ) : (
            <div className="evidence-grid">
              {evidence.map((item, index) => (
                <article className="evidence-card" key={`${item.product_id ?? 'evidence'}-${index}`}>
                  <h3>{item.product_id ?? 'unnamed product'}</h3>
                  <dl>
                    <div>
                      <dt>Source</dt>
                      <dd>{item.source ?? 'unknown'}</dd>
                    </div>
                    <div>
                      <dt>Freshness</dt>
                      <dd>{item.freshness ?? 'not reported'}</dd>
                    </div>
                  </dl>
                  <pre>{stringifyBounded(item.rows ?? item)}</pre>
                </article>
              ))}
            </div>
          )}
        </section>
      </section>
    </main>
  );
}
