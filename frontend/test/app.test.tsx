import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../src/App';
import '../src/styles.css';

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

type MetaFixture = {
  provider_mode: 'demo' | 'gemini' | 'anthropic';
  ready: boolean;
  supported_products: { product_id: string; title: string }[];
};

const ALL_SUPPORTED_PRODUCTS = [
  { product_id: 'weather_place_current_outlook', title: '장소별 현재 날씨 개황' },
  { product_id: 'weather_place_forecast_change_daily', title: '장소별 예보 변화' },
  { product_id: 'weather_place_precipitation_window', title: '장소별 강수 예상 시간대' },
  { product_id: 'weather_place_risk_window', title: '장소별 기상 위험 예상 시간대' },
];

const READY_META: MetaFixture = {
  provider_mode: 'demo',
  ready: true,
  supported_products: ALL_SUPPORTED_PRODUCTS,
};

function mockJson(body: unknown, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 503,
    json: async () => body,
  } as Response;
}

function installFetch({
  meta = READY_META,
  stream,
}: {
  meta?: unknown;
  stream?: Response;
} = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : input.toString();
    if (url === '/api/v1/meta') return mockJson(meta);
    if (url === '/api/v1/chat/stream' && stream) return stream;
    throw new Error(`예상하지 못한 테스트 요청: ${url}`);
  });
}

async function renderReady(meta: MetaFixture = READY_META) {
  installFetch({ meta });
  render(<App />);
  await waitFor(() => {
    expect(screen.getByRole('tablist', { name: '기상 질문 유형' })).toBeInTheDocument();
  });
}

type EvidenceFixture = {
  product_id: string;
  source: string;
  freshness: string;
  row_count: number;
  sample_only: boolean;
  rows: Record<string, unknown>[];
};

function successfulAgentStream(evidence: EvidenceFixture, answer = '근거 미리보기를 확인했습니다.') {
  return mockStream([
    'event: session.start\ndata: {"trace_id":"tr-weather-1","provider_mode":"demo"}\n\n',
    'event: assistant.status\ndata: {"trace_id":"tr-weather-1","status":"requesting_model_turn"}\n\n',
    'event: tool.call\ndata: {"trace_id":"tr-weather-1","tool_name":"search_products","input":{"q":"서울 날씨"}}\n\n',
    `event: assistant.final\ndata: ${JSON.stringify({
      trace_id: 'tr-weather-1',
      envelope: {
        answer,
        evidence_status: 'grounded_preview',
        evidence: [evidence],
      },
    })}\n\n`,
    'event: session.done\ndata: {"trace_id":"tr-weather-1","status":"completed"}\n\n',
  ]);
}

async function submitQuestion() {
  await userEvent.click(screen.getByRole('button', { name: '질문 보내기' }));
  await waitFor(() => {
    expect(screen.getByRole('article', { name: '서울 기상 데이터 답변' })).toBeInTheDocument();
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ASK Seoul 기상 에이전트 UI', () => {
  it('네 가지 기상 의도를 탭으로 선택하면 질문을 채우고 선택 상태를 알린다', async () => {
    await renderReady();

    const intentTabs = within(screen.getByRole('tablist', { name: '기상 질문 유형' })).getAllByRole('tab');
    expect(intentTabs).toHaveLength(4);
    expect(intentTabs.map((tab) => tab.textContent)).toEqual(expect.arrayContaining([
      expect.stringContaining('지금 날씨'),
      expect.stringContaining('예보 변화'),
      expect.stringContaining('비·눈 시간대'),
      expect.stringContaining('위험 시간대'),
    ]));

    const precipitationTab = screen.getByRole('tab', { name: /비·눈 시간대/ });
    await userEvent.click(precipitationTab);

    expect(precipitationTab).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('질문')).toHaveValue(
      '서울 주요 장소 중 비나 눈이 연속으로 예보된 시간대는 언제입니까?',
    );
  });

  it('서버 meta가 반환한 알려진 제품만 표시하고 첫 질문 전에도 실제 provider 모드를 보여준다', async () => {
    const fetchMock = installFetch({
      meta: {
        provider_mode: 'gemini',
        ready: true,
        supported_products: [
          ALL_SUPPORTED_PRODUCTS[1],
          ALL_SUPPORTED_PRODUCTS[3],
          { product_id: 'transit_parking_full_risk', title: '주차장 혼잡 위험' },
        ],
      },
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveAccessibleName('현재 실행 모드: LIVE · Gemini');
    });
    expect(screen.getByRole('status')).not.toHaveTextContent('LIVE · Gemini');
    expect(within(screen.getByRole('status')).getByText('LIVE 모드')).toHaveAttribute(
      'data-active',
      'true',
    );
    expect(within(screen.getByRole('status')).getByText('데모 모드')).toHaveAttribute(
      'data-active',
      'false',
    );
    const tabs = within(screen.getByRole('tablist', { name: '기상 질문 유형' })).getAllByRole('tab');
    expect(tabs).toHaveLength(2);
    expect(screen.getByRole('tab', { name: /예보 변화/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /위험 시간대/ })).toBeInTheDocument();
    expect(screen.queryByText(/주차장 혼잡/)).not.toBeInTheDocument();
    expect(screen.getByText('운영 중인 기상 제품 2개만 조회합니다')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '질문 보내기' })).toBeEnabled();
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/meta', expect.objectContaining({
      headers: { Accept: 'application/json' },
    }));
  });

  it('meta 연결 실패 시 오래된 제품 범위를 노출하지 않고 질문 전송을 막는다', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('network down'));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        '기상 에이전트 연결 상태를 확인하지 못했습니다. 새로고침 후 다시 시도해 주세요.',
      );
    });
    expect(screen.queryByRole('tablist', { name: '기상 질문 유형' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '질문 보내기' })).toBeDisabled();
  });

  it('meta 응답 계약이 잘못되면 제품 범위를 추측하지 않고 degraded 상태가 된다', async () => {
    installFetch({
      meta: {
        provider_mode: 'demo',
        ready: true,
        supported_products: 'weather_place_current_outlook',
      },
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        '기상 에이전트 연결 상태를 확인하지 못했습니다. 새로고침 후 다시 시도해 주세요.',
      );
    });
    expect(screen.queryByRole('tablist', { name: '기상 질문 유형' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '질문 보내기' })).toBeDisabled();
  });

  it('서버가 not-ready이면 실제 모드를 알리고 질문 전송을 막는다', async () => {
    installFetch({
      meta: {
        provider_mode: 'gemini',
        ready: false,
        supported_products: ALL_SUPPORTED_PRODUCTS,
      },
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        '현재 Gemini 에이전트를 사용할 수 없습니다. 서버 설정을 확인해 주세요.',
      );
    });
    expect(screen.getByRole('status')).toHaveAccessibleName('현재 실행 모드: LIVE · Gemini');
    expect(screen.getByRole('button', { name: '질문 보내기' })).toBeDisabled();
    for (const tab of screen.getAllByRole('tab')) expect(tab).toBeDisabled();
  });

  it('방향키와 Home·End로 탭 선택과 포커스를 함께 이동한다', async () => {
    await renderReady();

    const tabs = within(screen.getByRole('tablist', { name: '기상 질문 유형' })).getAllByRole('tab');
    expect(tabs.map((tab) => tab.tabIndex)).toEqual([0, -1, -1, -1]);
    expect(screen.getByRole('tabpanel', { name: /지금 날씨/ })).toBeInTheDocument();

    tabs[0].focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(tabs[1]).toHaveFocus();
    expect(tabs[1]).toHaveAttribute('aria-selected', 'true');
    expect(tabs.map((tab) => tab.tabIndex)).toEqual([-1, 0, -1, -1]);
    expect(screen.getByRole('tabpanel', { name: /예보 변화/ })).toBeInTheDocument();
    expect(screen.getByLabelText('질문')).toHaveValue(
      '서울 주요 장소의 오늘 예보는 직전 KMA 발표보다 어떻게 달라졌습니까?',
    );

    await userEvent.keyboard('{End}');
    expect(tabs[3]).toHaveFocus();
    expect(tabs[3]).toHaveAttribute('aria-selected', 'true');

    await userEvent.keyboard('{Home}');
    expect(tabs[0]).toHaveFocus();
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');

    await userEvent.keyboard('{ArrowLeft}');
    expect(tabs[3]).toHaveFocus();
    expect(tabs[3]).toHaveAttribute('aria-selected', 'true');
  });

  it('질문과 답변을 대화로 보여주고 trace는 접힌 실행 과정 안에서만 노출한다', async () => {
    const question = '폭염이나 호우 위험 시간대를 알려줘';
    const answer = '청운효자동은 15시 폭염 후보 구간으로 이동 시 주의가 필요합니다.';
    const evidence: EvidenceFixture = {
      product_id: 'weather_place_risk_window',
      source: 'https://ask-seoul.kr/api/v1/preview/weather_place_risk_window',
      freshness: '2026-08-20 11:23:50.065865',
      row_count: 1,
      sample_only: true,
      rows: [{
        product_row_id: 'seoul_admd_1111051500|2026-08-23T15:00:00.000000',
        place_name: '청운효자동',
        gu: '종로구',
        forecast_at: '2026-08-23 15:00:00',
        risk_labels: '폭염후보',
        temp_c: 33,
        precip_prob_pct: 10,
        wind_ms: 1,
      }],
    };
    const fetchMock = installFetch({ stream: successfulAgentStream(evidence, answer) });

    render(<App />);
    await screen.findByRole('tablist', { name: '기상 질문 유형' });
    await userEvent.clear(screen.getByLabelText('질문'));
    await userEvent.type(screen.getByLabelText('질문'), question);
    await submitQuestion();

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/chat/stream', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ question }),
    }));
    expect(within(screen.getByRole('article', { name: '내 질문' })).getByText(question)).toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: '서울 기상 데이터 답변' })).getByText(answer)).toBeInTheDocument();
    expect(within(screen.getByRole('banner')).queryByText(/추적 ID/)).not.toBeInTheDocument();

    const executionToggle = screen.getByRole('button', { name: '실행 과정 보기' });
    expect(screen.getByText('추적 ID tr-weather-1')).not.toBeVisible();
    await userEvent.click(executionToggle);
    expect(screen.getByText('추적 ID tr-weather-1')).toBeVisible();
    expect(screen.getByText('제품 검색')).toBeVisible();
    expect(screen.getByRole('button', { name: '실행 과정 접기' })).toBeInTheDocument();
  });

  it('제출 후 composer를 대화 아래로 옮기고 5행 근거와 안전 고지를 단계적으로 보여준다', async () => {
    const evidence: EvidenceFixture = {
      product_id: 'weather_place_risk_window',
      source: 'ASK Seoul preview',
      freshness: '2026-08-20 11:23:50.065865',
      row_count: 5,
      sample_only: true,
      rows: [
        { product_row_id: 'risk-1', place_name: '청운효자동', gu: '종로구', forecast_at: '2026-08-23 15:00:00', risk_labels: '폭염후보', temp_c: 33, precip_prob_pct: 10, wind_ms: 1 },
        { product_row_id: 'risk-2', place_name: '사직동', gu: '종로구', forecast_at: '2026-08-23 15:00:00', risk_labels: '폭염후보', temp_c: 33, precip_prob_pct: 10, wind_ms: 1 },
        { product_row_id: 'risk-3', place_name: '삼청동', gu: '종로구', forecast_at: '2026-08-23 15:00:00', risk_labels: '폭염후보', temp_c: 33, precip_prob_pct: 10, wind_ms: 1 },
        { product_row_id: 'risk-4', place_name: '부암동', gu: '종로구', forecast_at: '2026-08-23 15:00:00', risk_labels: '폭염후보', temp_c: 33, precip_prob_pct: 10, wind_ms: 1 },
        { product_row_id: 'risk-5', place_name: '평창동', gu: '종로구', forecast_at: '2026-08-23 15:00:00', risk_labels: '폭염후보', temp_c: 33, precip_prob_pct: 10, wind_ms: 1 },
      ],
    };
    installFetch({
      stream: successfulAgentStream(
        evidence,
        '주의: 공개 미리보기 최대 5행을 근거로 한 답변입니다.\n\n종로구 다섯 장소에 폭염 후보가 확인됐습니다.',
      ),
    });
    render(<App />);
    await screen.findByRole('tablist', { name: '기상 질문 유형' });

    const composerBefore = screen.getByRole('form', { name: '기상 질문' });
    const conversationBefore = screen.getByRole('region', { name: '기상 데이터 대화' });
    expect(composerBefore.compareDocumentPosition(conversationBefore) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await submitQuestion();

    const conversationAfter = screen.getByRole('region', { name: '기상 데이터 대화' });
    const composerAfter = screen.getByRole('form', { name: '기상 질문' });
    expect(conversationAfter.compareDocumentPosition(composerAfter) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const followupComposer = composerAfter.parentElement;
    expect(followupComposer).toHaveClass('followup-composer');
    expect(getComputedStyle(followupComposer!).marginLeft).toBe('auto');

    const answerArticle = screen.getByRole('article', { name: '서울 기상 데이터 답변' });
    const summary = within(answerArticle).getByRole('region', { name: '핵심 요약' });
    expect(summary).toHaveTextContent('종로구 다섯 장소에 폭염 후보가 확인됐습니다.');
    expect(summary).not.toHaveTextContent('주의:');
    expect(within(answerArticle).getByRole('note')).toHaveTextContent(
      '공개 미리보기 최대 5행을 근거로 한 답변입니다.',
    );

    const evidenceRegion = within(answerArticle).getByRole('region', { name: '근거 확인' });
    expect(within(evidenceRegion).getByText('청운효자동')).toBeVisible();
    expect(within(evidenceRegion).getByText('삼청동')).toBeVisible();
    expect(within(evidenceRegion).queryByText('부암동')).not.toBeInTheDocument();
    expect(within(evidenceRegion).queryByText('평창동')).not.toBeInTheDocument();

    await userEvent.click(within(evidenceRegion).getByRole('button', { name: '2행 더 보기' }));
    expect(within(evidenceRegion).getByText('부암동')).toBeVisible();
    expect(within(evidenceRegion).getByText('평창동')).toBeVisible();
    expect(within(evidenceRegion).getByRole('button', { name: '2행 접기' })).toBeInTheDocument();
  });

  it.each([
    {
      tabName: /지금 날씨/,
      productTitle: '장소별 현재 날씨 개황',
      evidence: {
        product_id: 'weather_place_current_outlook',
        source: 'ASK Seoul preview',
        freshness: '2026-08-20 11:23:50.065865',
        row_count: 1,
        sample_only: true,
        rows: [{ product_row_id: 'seoul_admd_1111051500', place_name: '청운효자동', gu: '종로구', forecast_at: '2026-08-20 12:00:00', sky_label: '맑음', pty_label: '강수 없음', temp_c: 29, humidity_pct: 70, precip_prob_pct: 10 }],
      },
      visibleCells: ['청운효자동', '맑음 · 강수 없음', '29℃', '70%', '10%'],
    },
    {
      tabName: /예보 변화/,
      productTitle: '장소별 예보 변화',
      evidence: {
        product_id: 'weather_place_forecast_change_daily',
        source: 'ASK Seoul preview',
        freshness: '2026-08-20 11:23:50.065865',
        row_count: 1,
        sample_only: true,
        rows: [{ product_row_id: 'seoul_admd_1111051500|2026-08-23', place_name: '청운효자동', gu: '종로구', forecast_date: '2026-08-23', change_state: 'changed', previous_min_temp_c: 25, latest_min_temp_c: 24, previous_max_temp_c: 35, latest_max_temp_c: 33, previous_max_precip_prob_pct: 50, latest_max_precip_prob_pct: 30 }],
      },
      visibleCells: ['청운효자동', '변경됨', '25℃ → 24℃', '35℃ → 33℃', '50% → 30%'],
    },
    {
      tabName: /비·눈 시간대/,
      productTitle: '장소별 강수 예상 시간대',
      evidence: {
        product_id: 'weather_place_precipitation_window',
        source: 'ASK Seoul preview',
        freshness: '2026-08-20 11:23:50.065865',
        row_count: 1,
        sample_only: true,
        rows: [{ product_row_id: 'seoul_admd_1111053000|2026-08-21T08:00:00.000000', place_name: '사직동', gu: '종로구', window_start_at: '2026-08-21 08:00:00', window_end_at: '2026-08-21 15:00:00', precipitation_hour_count: 8, precip_prob_max_pct: 60 }],
      },
      visibleCells: ['사직동', '2026-08-21 08:00', '2026-08-21 15:00', '8시간', '60%'],
    },
    {
      tabName: /위험 시간대/,
      productTitle: '장소별 기상 위험 예상 시간대',
      evidence: {
        product_id: 'weather_place_risk_window',
        source: 'ASK Seoul preview',
        freshness: '2026-08-20 11:23:50.065865',
        row_count: 1,
        sample_only: true,
        rows: [{ product_row_id: 'seoul_admd_1111051500|2026-08-23T15:00:00.000000', place_name: '청운효자동', gu: '종로구', forecast_at: '2026-08-23 15:00:00', risk_labels: '폭염후보', temp_c: 33, precip_prob_pct: 10, wind_ms: 1 }],
      },
      visibleCells: ['청운효자동', '폭염후보', '33℃', '10%', '1m/s'],
    },
  ])('$productTitle 근거를 원본 JSON 대신 한국어 표로 먼저 보여준다', async ({ tabName, productTitle, evidence, visibleCells }) => {
    installFetch({ stream: successfulAgentStream(evidence) });
    render(<App />);
    await screen.findByRole('tablist', { name: '기상 질문 유형' });
    await userEvent.click(screen.getByRole('tab', { name: tabName }));
    await submitQuestion();

    const evidenceRegion = screen.getByRole('region', { name: '근거 확인' });
    expect(within(evidenceRegion).getByText(productTitle)).toBeInTheDocument();
    for (const cell of visibleCells) {
      expect(within(evidenceRegion).getByText(cell)).toBeVisible();
    }

    const rawToggle = within(evidenceRegion).getByRole('button', { name: '원본 JSON 보기' });
    expect(within(evidenceRegion).getByText(/product_row_id/)).not.toBeVisible();
    await userEvent.click(rawToggle);
    expect(within(evidenceRegion).getByText(/product_row_id/)).toBeVisible();
    expect(within(evidenceRegion).getByRole('button', { name: '원본 JSON 접기' })).toBeInTheDocument();
  });

  it('영문 upstream 오류를 한국어 사용자에게 노출하지 않는다', async () => {
    installFetch({
      stream: mockStream(['event: session.error\ndata: {"error":{"code":"upstream_unavailable","message":"Upstream request timed out.","retryable":true}}\n\n']),
    });

    render(<App />);
    await screen.findByRole('tablist', { name: '기상 질문 유형' });
    await userEvent.click(screen.getByRole('button', { name: '질문 보내기' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        'ASK Seoul 기상 데이터 서비스에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.',
      );
    });
    expect(screen.getByRole('alert')).not.toHaveTextContent('Upstream request timed out.');
  });

  it('모델 요청 한도 오류를 재시도 시점을 판단할 수 있는 한국어로 안내한다', async () => {
    installFetch({
      stream: mockStream(['event: session.error\ndata: {"error":{"code":"provider_rate_limited","message":"Gemini provider rate limited the request.","retryable":true}}\n\n']),
    });

    render(<App />);
    await screen.findByRole('tablist', { name: '기상 질문 유형' });
    await userEvent.click(screen.getByRole('button', { name: '질문 보내기' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        'AI 모델의 요청 한도에 도달했습니다. 잠시 후 또는 사용량이 초기화된 뒤 다시 시도해 주세요.',
      );
    });
    expect(screen.getByRole('alert')).not.toHaveTextContent('Gemini provider rate limited the request.');
  });
});
