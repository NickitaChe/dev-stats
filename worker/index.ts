interface AssetBinding {
  fetch(request: Request): Promise<Response>;
}

interface Env {
  ASSETS: AssetBinding;
}

interface StatsPayload {
  generatedAt: string | null;
  totals: Record<string, string | number | null>;
  [key: string]: unknown;
}

const METRICS: Record<string, string> = {
  repositories: 'repositories',
  commits: 'commits',
  'lines-added': 'linesAdded',
  'lines-deleted': 'linesDeleted',
  'net-lines': 'netLines',
  'active-days': 'activeDays'
};

const corsHeaders = (): Headers => {
  const headers = new Headers();
  headers.set('Access-Control-Allow-Origin', '*');
  headers.set('Access-Control-Allow-Methods', 'GET, OPTIONS');
  headers.set('Access-Control-Allow-Headers', 'Content-Type');
  headers.set('Cache-Control', 'public, max-age=300');
  return headers;
};

const json = (value: unknown, status = 200): Response => {
  const headers = corsHeaders();
  headers.set('Content-Type', 'application/json; charset=utf-8');
  return new Response(JSON.stringify(value, null, 2), { status, headers });
};

const text = (value: string, status = 200): Response => {
  const headers = corsHeaders();
  headers.set('Content-Type', 'text/plain; charset=utf-8');
  return new Response(value, { status, headers });
};

const readStats = async (request: Request, env: Env): Promise<StatsPayload> => {
  const assetUrl = new URL(request.url);
  assetUrl.pathname = '/data/stats.json';
  assetUrl.search = '';

  const response = await env.ASSETS.fetch(
    new Request(assetUrl.toString(), {
      method: 'GET',
      headers: request.headers
    })
  );

  if (!response.ok) {
    throw new Error(`stats asset returned HTTP ${response.status}`);
  }

  return await response.json() as StatsPayload;
};

const apiIndex = (): Response =>
  json({
    service: 'dev-stats',
    endpoints: {
      stats: '/api/stats',
      summary: '/api/stats/summary',
      metric: '/api/stats/{metric}',
      textMetric: '/api/stats/{metric}?format=text',
      health: '/api/health'
    },
    metrics: Object.keys(METRICS)
  });

const worker = {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS' && url.pathname.startsWith('/api/')) {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    if (request.method !== 'GET' && url.pathname.startsWith('/api/')) {
      return json({ error: 'method_not_allowed' }, 405);
    }

    if (url.pathname === '/api') {
      return apiIndex();
    }

    if (url.pathname === '/api/health') {
      return json({ status: 'ok' });
    }

    if (url.pathname === '/api/stats') {
      try {
        return json(await readStats(request, env));
      } catch (error) {
        return json(
          {
            error: 'stats_unavailable',
            message: error instanceof Error ? error.message : 'unknown error'
          },
          503
        );
      }
    }

    if (url.pathname.startsWith('/api/stats/')) {
      const metric = decodeURIComponent(url.pathname.slice('/api/stats/'.length));

      try {
        const stats = await readStats(request, env);

        if (metric === 'summary') {
          return json({
            generatedAt: stats.generatedAt,
            totals: stats.totals
          });
        }

        const property = METRICS[metric];
        if (!property) {
          return json(
            {
              error: 'unknown_metric',
              supported: Object.keys(METRICS)
            },
            404
          );
        }

        const value = stats.totals[property];

        if (url.searchParams.get('format') === 'text') {
          return text(value == null ? '' : String(value));
        }

        return json({
          metric,
          value,
          generatedAt: stats.generatedAt
        });
      } catch (error) {
        return json(
          {
            error: 'stats_unavailable',
            message: error instanceof Error ? error.message : 'unknown error'
          },
          503
        );
      }
    }

    return env.ASSETS.fetch(request);
  }
};

export default worker;
