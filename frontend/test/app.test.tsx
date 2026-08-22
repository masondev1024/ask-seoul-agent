import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../src/App';

class MockReader {
  private index = 0;

  constructor(private readonly chunks: string[]) {}

  async read(): Promise<ReadableStreamReadResult<Uint8Array>> {
    if (this.index >= this.chunks.length) {
      return { done: true, value: undefined };
    }
    const value = new TextEncoder().encode(this.chunks[this.index]);
    this.index += 1;
    return { done: false, value };
  }
}

function mockStream(chunks: string[]) {
  const reader = new MockReader(chunks);
  return {
    ok: true,
    body: {
      getReader: () => reader,
    },
  } as unknown as Response;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Ask Seoul agent UI', () => {
  it('submits a question and renders streamed agent events with evidence', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      mockStream([
        'event: session.start\ndata: {"trace_id":"tr-1","provider_mode":"demo"}\n\n',
        'event: tool.call\ndata: {"trace_id":"tr-1","tool_name":"search_products","input":{"q":"weather"}}\n\n',
        'event: tool.result\ndata: {"trace_id":"tr-1","tool_name":"preview_product","output":{"row_count":1,"sample_only":true}}\n\n',
        'event: assistant.final\ndata: {"trace_id":"tr-1","envelope":{"answer":"Weather risk is highest around Jongno.","evidence_status":"grounded_preview","evidence":[{"product_id":"weather_place_risk_window","source":"ASK Seoul preview","freshness":"sample-only","rows":[{"dong":"Jongno","risk":"rain"}]}]}}\n\n',
        'event: session.done\ndata: {"trace_id":"tr-1","status":"completed"}\n\n',
      ]),
    );

    render(<App />);
    await userEvent.type(screen.getByLabelText(/question/i), 'Where is weather risk high?');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText('Weather risk is highest around Jongno.')).toBeInTheDocument();
    });

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/chat/stream', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ question: 'Where is weather risk high?' }),
    }));
    expect(screen.getByText(/demo mode/i)).toBeInTheDocument();
    expect(screen.getByText('tool.call')).toBeInTheDocument();
    const answerPanel = screen.getByRole('article', { name: /answer/i });
    expect(within(answerPanel).getByText('completed')).toBeInTheDocument();
    expect(screen.getByText(/five-row sample-only in every mode/i)).toBeInTheDocument();
    expect(screen.getByText('weather_place_risk_window')).toBeInTheDocument();
    const evidencePanel = screen.getByRole('region', { name: /evidence/i });
    expect(within(evidencePanel).getByText(/sample-only/i)).toBeInTheDocument();
    expect(within(evidencePanel).getByText(/Jongno/i)).toBeInTheDocument();
  });

  it('shows a UI error when a streamed event contains invalid JSON', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      mockStream(['event: session.start\ndata: {"trace_id":"tr-2"}\n\nevent: assistant.final\ndata: {bad json}\n\n']),
    );

    render(<App />);
    await userEvent.type(screen.getByLabelText(/question/i), 'bad stream');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/invalid stream payload/i);
    });
  });
});
